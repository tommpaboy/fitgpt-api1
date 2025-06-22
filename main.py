from pathlib import Path

# Innehållet för main.py med Flask och alla nödvändiga endpoints
main_py_content = '''
import os
import json
import base64
from flask import Flask, request, jsonify, redirect
import requests
from urllib.parse import urlencode
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"

SCOPES = [
    "activity", "sleep", "profile", "heartrate", "location", "nutrition",
    "oxygen_saturation", "respiratory_rate", "settings", "social",
    "temperature", "weight"
]

@app.route("/")
def home():
    query = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "expires_in": "604800"
    })
    return redirect(f"{AUTHORIZE_URL}?{query}")

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Ingen kod mottagen", 400

    data = {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code
    }

    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        return f"Fel vid tokenhämtning: {response.text}", 400

    tokens = response.json()
    with open("tokens.json", "w") as f:
        json.dump(tokens, f)

    return f"Token mottagen och sparad: {tokens}"

def get_token():
    if not Path("tokens.json").exists():
        return None
    with open("tokens.json", "r") as f:
        return json.load(f).get("access_token")

def get_fitbit_data(endpoint):
    token = get_token()
    if not token:
        return {"error": "Ingen token tillgänglig."}, 401
    url = f"https://api.fitbit.com/1/user/-/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    try:
        return response.json(), response.status_code
    except Exception:
        return {"error": "Fel vid hämtning."}, 500

@app.route("/data", methods=["GET"])
def all_data():
    date = request.args.get("date", datetime.today().strftime("%Y-%m-%d"))
    endpoints = {
        "activity": f"activities/date/{date}.json",
        "steps": f"activities/steps/date/{date}/1d.json",
        "calories": f"activities/calories/date/{date}/1d.json",
        "distance": f"activities/distance/date/{date}/1d.json",
        "floors": f"activities/floors/date/{date}/1d.json",
        "elevation": f"activities/elevation/date/{date}/1d.json",
        "heartrate": f"activities/heart/date/{date}/1d.json",
        "sleep": f"sleep/date/{date}.json"
    }

    all_results = {}
    for key, endpoint in endpoints.items():
        result, _ = get_fitbit_data(endpoint)
        all_results[key] = result

    return jsonify(all_results)

if __name__ == "__main__":
    app.run(debug=True, port=10000)
'''

# Spara som main.py
main_path = Path("/mnt/data/main.py")
main_path.name
