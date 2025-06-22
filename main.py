from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
import os
from datetime import date
from dotenv import load_dotenv

# Ladda miljövariabler
load_dotenv()

# Initiera app
app = FastAPI()

# Mounta .well-known-mappen för GPT-plugin
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

# Miljövariabler
FITBIT_CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE = "fitbit_token.json"

# Startsida
@app.get("/", response_class=HTMLResponse)
def home():
    return "<h1>FitGPT är igång! 🚀</h1>"

# Auth-länk
@app.get("/authorize")
def authorize():
    scope = (
        "activity nutrition sleep heartrate weight location profile settings social "
        "temperature oxygen_saturation respiratory_rate"
    )
    url = (
        f"https://www.fitbit.com/oauth2/authorize?response_type=code&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={scope}"
    )
    return {"auth_url": url}

# Callback efter Fitbit-login
@app.get("/callback")
def callback(code: str):
    token_url = "https://api.fitbit.com/oauth2/token"
    headers = {
        "Authorization": f"Basic {requests.auth._basic_auth_str(FITBIT_CLIENT_ID, FITBIT_CLIENT_SECRET)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "client_id": FITBIT_CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    response = requests.post(token_url, headers=headers, data=data)
    if response.status_code != 200:
        return {"error": "Tokenförfrågan misslyckades", "details": response.text}

    token_data = response.json()

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

    return {"message": "Token mottagen och sparad!", "token_data": token_data}

# Funktion för att hämta data från Fitbit
def get_fitbit_data(resource_path, date_str, user_id="BD96M2"):
    if not os.path.exists(TOKEN_FILE):
        return {"error": "Ingen token sparad ännu"}

    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)

    access_token = token_data.get("access_token")
    if not access_token:
        return {"error": "Access token saknas"}

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.fitbit.com/1/user/{user_id}/{resource_path}/date/{date_str}/1d.json"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": f"Fel vid hämtning: {response.status_code}", "details": response.text}

    return response.json()

# Kombinera flera datatyper i ett svar
@app.get("/data")
def get_combined_data():
    today = date.today().isoformat()
    user_id = "BD96M2"

    return {
        "steps": get_fitbit_data("activities/steps", today, user_id),
        "calories": get_fitbit_data("activities/calories", today, user_id),
        "sleep": get_fitbit_data("sleep", today, user_id),
        "heart": get_fitbit_data("activities/heart", today, user_id),
        "distance": get_fitbit_data("activities/distance", today, user_id),
        "floors": get_fitbit_data("activities/floors", today, user_id),
        "elevation": get_fitbit_data("activities/elevation", today, user_id),
    }
