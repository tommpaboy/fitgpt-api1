from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
import os
import base64
import time
from datetime import date, timedelta
from dotenv import load_dotenv

# Ladda miljövariabler från .env
load_dotenv()

app = FastAPI()
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

FITBIT_CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE = "fitbit_token.json"

@app.get("/", response_class=HTMLResponse)
def home():
    return "<h1>FitGPT är igång! 🚀</h1><p><a href='/authorize'>Logga in med Fitbit</a></p>"

@app.get("/authorize")
def authorize():
    url = (
        "https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=activity%20nutrition%20sleep%20heartrate%20weight%20location%20profile%20settings%20social%20temperature%20oxygen_saturation%20respiratory_rate"
    )
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    token_url = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "client_id": FITBIT_CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    response = requests.post(token_url, headers=headers, data=data)

    try:
        token_data = response.json()
    except Exception as e:
        return {
            "message": "❌ Kunde inte tolka svaret från Fitbit.",
            "error": str(e),
            "raw_response": response.text,
        }

    if "access_token" in token_data:
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        return {"message": "✅ Token mottagen och sparad!", "token_data": token_data}
    else:
        return {"message": "⚠️ Något gick fel vid tokenutbyte.", "token_data": token_data}

def refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None

    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)

    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    test_response = requests.get("https://api.fitbit.com/1/user/-/profile.json", headers=headers)

    if test_response.status_code == 200:
        return token_data

    print("🔁 Förnyar token...")
    token_url = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": token_data["refresh_token"],
    }

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code == 200:
        new_token_data = response.json()
        with open(TOKEN_FILE, "w") as f:
            json.dump(new_token_data, f)
        return new_token_data
    else:
        print("❌ Kunde inte förnya token:", response.text)
        return None

def get_fitbit_data(resource_path, start_date, end_date):
    token_data = refresh_token_if_needed()
    if not token_data:
        return {"error": "Ingen giltig token hittades."}

    access_token = token_data.get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "5"))
            print(f"⏳ Rate limit – väntar {retry_after} sekunder...")
            time.sleep(retry_after)
            response = requests.get(url, headers=headers)

        response.raise_for_status()
        data = response.json()
        if not data:
            return {"data": {}, "message": "Inget registrerat för denna dag"}
        return data
    except Exception as e:
        print(f"⚠️ Fel vid hämtning av {resource_path}: {e}")
        return {"data": {}, "error": str(e)}

@app.get("/steps")
def get_steps(date: str = None):
    if not date:
        date = date.today().isoformat()
    return get_fitbit_data("activities/steps", date, date)

@app.get("/sleep")
def get_sleep(date: str = None):
    if not date:
        date = date.today().isoformat()
    return get_fitbit_data("sleep", date, date)

@app.get("/calories")
def get_calories(date: str = None):
    if not date:
        date = date.today().isoformat()
    return get_fitbit_data("activities/calories", date, date)

@app.get("/heart")
def get_heart(date: str = None):
    if not date:
        date = date.today().isoformat()
    return get_fitbit_data("activities/heart", date, date)
