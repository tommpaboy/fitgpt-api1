import os
import json
import base64
from flask import Flask, redirect, request, jsonify, send_from_directory
import requests
from urllib.parse import urlencode
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# === KONFIGURATION ===
CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = "https://fitgpt-2364.onrender.com/callback"
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"

SCOPES = [
    "activity", "sleep", "profile", "heartrate", "location", "nutrition",
    "oxygen_saturation", "respiratory_rate", "settings", "social",
    "temperature", "weight"
]

# === HJÄLPFUNKTIONER ===
def get_stored_tokens():
    if os.path.exists("tokens.json"):
        with open("tokens.json", "r") as f:
            return json.load(f)
    return None

def get_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}

def get_fitbit_data(access_token, endpoint):
    url = f"https://api.fitbit.com/1/user/-/{endpoint}"
    response = requests.get(url, headers=get_headers(access_token))
    if response.status_code == 200:
        return {"data": response.json(), "success": True}
    else:
        return {"error": response.text, "success": False}

# === ROUTER ===
@app.route("/")
def home():
    query = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "expires_in": "604800"
    })
    auth_url = f"{AUTHORIZE_URL}?{query}"
    return f'<a href="{auth_url}">Anslut Fitbit</a>'

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Ingen kod mottagen.", 400

    data = {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    response = requests.post(TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        return f"Fel vid tokenförfrågan: {response.text}", 400

    tokens = response.json()

    with open("tokens.json", "w") as f:
        json.dump(tokens, f)

    return f"Token mottagen och sparad: {tokens}"

@app.route("/data", methods=["GET"])
def data():
    tokens = get_stored_tokens()
    if not tokens:
        return jsonify({"error": "Ingen token sparad."}), 401

    access_token = tokens["access_token"]
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

    endpoints = {
        "activity": f"activities/date/{date}.json",
        "sleep": f"sleep/date/{date}.json",
        "profile": "profile.json",
        "heartrate": f"activities/heart/date/{date}/1d/1min.json",
        "location": "devices.json",
        "nutrition": f"foods/log/date/{date}.json",
        "oxygen_saturation": f"spo2/date/{date}.json",
        "respiratory_rate": f"br/date/{date}.json",
        "settings": "settings.json",
        "social": "friends.json",
        "temperature": f"body/log/skinTemperature/date/{date}.json",
        "weight": f"body/log/weight/date/{date}.json"
    }

    fitbit_data = {}
    for key, endpoint in endpoints.items():
        fitbit_data[key] = get_fitbit_data(access_token, endpoint)

    return jsonify({"fitbit_data": fitbit_data})

@app.route("/.well-known/<path:filename>")
def well_known(filename):
    return send_from_directory(".well-known", filename)

if __name__ == "__main__":
    app.run()
