from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import requests
import json
import os
from datetime import date
from dotenv import load_dotenv

# Initiera appen
app = FastAPI()

# Ladda miljövariabler
load_dotenv()

# Autentiseringsuppgifter
FITBIT_CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE = "fitbit_token.json"


@app.get("/", response_class=HTMLResponse)
def home():
    return "<h1>FitGPT är igång! 🚀</h1>"


@app.get("/login")
def login():
    url = (
        f"https://www.fitbit.com/oauth2/authorize?response_type=code&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=activity%20nutrition%20sleep%20heartrate%20weight%20location%20profile%20settings"
        f"%20social%20temperature%20oxygen_saturation%20respiratory_rate"
    )
    return RedirectResponse(url)


@app.get("/authorize")
def authorize():
    url = (
        f"https://www.fitbit.com/oauth2/authorize?response_type=code&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=activity%20nutrition%20sleep%20heartrate%20weight%20location%20profile%20settings"
        f"%20social%20temperature%20oxygen_saturation%20respiratory_rate"
    )
    return {"auth_url": url}


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
    token_data = response.json()

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

    return {
        "message": "✅ Token mottagen och sparad!",
        "token_data": token_data,
    }


def get_fitbit_data(resource_path, date_str, user_id="BD96M2"):
    try:
        with open(TOKEN_FILE, "r") as f:
            token_data = json.load(f)
    except FileNotFoundError:
        return {"error": "❌ Token saknas – logga in först via /login"}

    access_token = token_data.get("access_token")
    if not access_token:
        return {"error": "❌ Access token saknas"}

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.fitbit.com/1/user/{user_id}/{resource_path}/date/{date_str}/1d.json"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return {
            "error": f"Fel vid hämtning: {response.status_code}",
            "details": response.text,
        }

    return response.json()


@app.get("/data")
def get_combined_data():
    today = date.today().isoformat()
    user_id = "BD96M2"

    steps = get_fitbit_data("activities/steps", today, user_id)
    calories = get_fitbit_data("activities/calories", today, user_id)
    sleep = get_fitbit_data("sleep", today, user_id)
    heart = get_fitbit_data("activities/heart", today, user_id)

    return {
        "steps": steps,
        "calories": calories,
        "sleep": sleep,
        "heart": heart,
    }
