import os
import json
import requests
from flask import Flask, request, redirect, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

CLIENT_ID = os.environ.get("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.environ.get("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("FITBIT_REDIRECT_URI") or "https://fitgpt-2364.onrender.com/callback"

TOKEN_FILE = "token.json"
FITBIT_API = "https://api.fitbit.com"

# ----------------------
#   Hjälpfunktioner
# ----------------------

def save_token(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)

def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def refresh_token():
    token = load_token()
    if not token or "refresh_token" not in token:
        return None

    auth_header = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    }

    res = requests.post(f"{FITBIT_API}/oauth2/token", auth=auth_header, data=payload)
    if res.status_code == 200:
        new_token = res.json()
        save_token(new_token)
        return new_token
    return None

def get_access_token():
    token = load_token()
    if not token:
        return None
    if token.get("expires_in", 0) < 100:
        token = refresh_token()
    return token.get("access_token")

def fitbit_get(path):
    access_token = get_access_token()
    if not access_token:
        return {"error": "Ingen access token."}, 401

    headers = {"Authorization": f"Bearer {access_token}"}
    res = requests.get(f"{FITBIT_API}{path}", headers=headers)

    if res.status_code == 200:
        return res.json()
    return {"error": res.text}, res.status_code

# ----------------------
#   Flask-rutter
# ----------------------

@app.route("/")
def index():
    return "FitGPT är igång! 🚀"

@app.route("/authorize")
def authorize():
    scope = "activity heartrate location nutrition oxygen_saturation profile respiratory_rate sleep social temperature weight"
    url = (
        f"https://www.fitbit.com/oauth2/authorize?"
        f"response_type=code&client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&scope={scope}&expires_in=604800"
    )
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Ingen kod mottagen", 400

    auth_header = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    payload = {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    res = requests.post(f"{FITBIT_API}/oauth2/token", auth=auth_header, data=payload)
    if res.status_code == 200:
        token_data = res.json()
        save_token(token_data)
        return f"Token mottagen och sparad: {token_data}"
    return f"Fel vid tokenutbyte: {res.text}", 400

@app.route("/days")
def list_days():
    today = datetime.now()
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    return jsonify(days)

@app.route("/data/<date>")
def get_data_for_date(date):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"error": "Ogiltigt datumformat, använd YYYY-MM-DD."}, 400

    data = {
        "steps": fitbit_get(f"/1/user/-/activities/steps/date/{date}/1d.json"),
        "calories": fitbit_get(f"/1/user/-/activities/calories/date/{date}/1d.json"),
        "heart": fitbit_get(f"/1/user/-/activities/heart/date/{date}/1d.json"),
        "distance": fitbit_get(f"/1/user/-/activities/distance/date/{date}/1d.json"),
        "sleep": fitbit_get(f"/1.2/user/-/sleep/date/{date}.json"),
        "activities": fitbit_get(f"/1/user/-/activities/list.json?afterDate={date}&sort=desc&limit=10&offset=0"),
    }
    return jsonify(data)

@app.route("/status")
def status():
    return {"status": "FitGPT lever!", "timestamp": datetime.now().isoformat()}

# ----------------------
#   Kör servern lokalt
# ----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
