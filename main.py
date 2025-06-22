import os
import json
import base64
import requests
from flask import Flask, redirect, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = "https://fitgpt-2364.onrender.com/callback"
AUTHORIZE_URL = "https://www.fitbit.com/oauth2/authorize"
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
API_BASE = "https://api.fitbit.com/1/user/-/"
SCOPES = [
    "activity", "sleep", "profile", "heartrate", "location", "nutrition",
    "oxygen_saturation", "respiratory_rate", "settings", "social",
    "temperature", "weight"
]

TOKENS_FILE = "tokens.json"

def get_access_token():
    if not os.path.exists(TOKENS_FILE):
        return None
    with open(TOKENS_FILE) as f:
        return json.load(f).get("access_token")

@app.route("/")
def home():
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "expires_in": "604800"
    }
    url = f"{AUTHORIZE_URL}?{requests.compat.urlencode(query)}"
    return f"<h3>🔐 Anslut din Fitbit:</h3><a href='{url}'>Logga in</a>"

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
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        return f"Token-fel: {response.text}", 400

    with open(TOKENS_FILE, "w") as f:
        json.dump(response.json(), f)

    return f"✅ Token mottagen och sparad: {response.json()}"

@app.route("/days")
def list_days():
    from datetime import datetime, timedelta
    today = datetime.today()
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    return jsonify({"days": days})

@app.route("/data/<date>")
def get_data(date):
    token = get_access_token()
    if not token:
        return jsonify({"error": "Ingen token"}), 403

    headers = {"Authorization": f"Bearer {token}"}

    def safe_get(url):
        r = requests.get(url, headers=headers)
        return r.json() if r.status_code == 200 else {}

    steps = safe_get(f"{API_BASE}activities/date/{date}.json")
    sleep = safe_get(f"https://api.fitbit.com/1.2/user/-/sleep/date/{date}.json")
    hr = safe_get(f"{API_BASE}activities/heart/date/{date}/1d.json")
    cal = safe_get(f"{API_BASE}activities/date/{date}.json")

    return jsonify({
        "date": date,
        "steps": steps.get("summary", {}).get("steps"),
        "calories": steps.get("summary", {}).get("caloriesOut"),
        "sleep": sleep.get("summary", {}),
        "heart_rate": hr.get("activities-heart", [{}])[0].get("value", {}),
    })

@app.route("/.well-known/ai-plugin.json")
def serve_manifest():
    return send_from_directory(".well-known", "ai-plugin.json")

@app.route("/.well-known/openapi.yaml")
def serve_openapi():
    return send_from_directory(".well-known", "openapi.yaml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
