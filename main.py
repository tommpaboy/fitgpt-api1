from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
import os
import base64
from datetime import date, timedelta
from dotenv import load_dotenv

# Ladda miljövariabler från .env
load_dotenv()

app = FastAPI()

# Montera statisk mapp för GPT-plugin
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

# Hämta miljövariabler
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
        f"https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=activity%20nutrition%20sleep%20heartrate%20weight%20location%20profile%20settings%20social%20temperature%20oxygen_saturation%20respiratory_rate"
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
        return {
            "message": "✅ Token mottagen och sparad!",
            "token_data": token_data
        }
    else:
        return {
            "message": "⚠️ Något gick fel vid tokenutbyte.",
            "token_data": token_data
        }

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
        return {"error": "Ingen giltig token hittades. Logga in först."}

    access_token = token_data.get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": f"Fel vid hämtning: {response.status_code}", "details": response.text}

    return response.json()

@app.get("/data")
def get_combined_data(days: int = 1):
    today = date.today()
    start_date = (today - timedelta(days=days - 1)).isoformat()
    end_date = today.isoformat()

    return {
        "from": start_date,
        "to": end_date,
        "steps": get_fitbit_data("activities/steps", start_date, end_date),
        "calories": get_fitbit_data("activities/calories", start_date, end_date),
        "sleep": get_fitbit_data("sleep", start_date, end_date),
        "heart": get_fitbit_data("activities/heart", start_date, end_date),
    }

@app.get("/data/extended")
def get_extended_data(days: int = 1, target_date: str = None):  # <-- FIX: renamed `date` to `target_date`
    if target_date:
        start_date = end_date = target_date
    else:
        today = date.today()
        start_date = (today - timedelta(days=days - 1)).isoformat()
        end_date = today.isoformat()

    return {
        "from": start_date,
        "to": end_date,
        # 🏋️ Träning
        "steps": get_fitbit_data("activities/steps", start_date, end_date),
        "calories": get_fitbit_data("activities/calories", start_date, end_date),
        "active_calories": get_fitbit_data("activities/activityCalories", start_date, end_date),
        "activity_log": get_fitbit_data("activities", start_date, end_date),
        "distance": get_fitbit_data("activities/distance", start_date, end_date),
        "floors": get_fitbit_data("activities/floors", start_date, end_date),
        "elevation": get_fitbit_data("activities/elevation", start_date, end_date),
        "activity_levels": {
            "sedentary": get_fitbit_data("activities/minutesSedentary", start_date, end_date),
            "lightly_active": get_fitbit_data("activities/minutesLightlyActive", start_date, end_date),
            "fairly_active": get_fitbit_data("activities/minutesFairlyActive", start_date, end_date),
            "very_active": get_fitbit_data("activities/minutesVeryActive", start_date, end_date),
        },

        # ❤️ Puls & hälsa
        "heart": get_fitbit_data("activities/heart", start_date, end_date),
        "spo2": get_fitbit_data("spo2", start_date, end_date),
        "breathing_rate": get_fitbit_data("br", start_date, end_date),
        "core_temp": get_fitbit_data("temp/core", start_date, end_date),
        "skin_temp": get_fitbit_data("temp/skin", start_date, end_date),
        "stress": get_fitbit_data("body/stressManagement", start_date, end_date),

        # 😴 Sömn
        "sleep": get_fitbit_data("sleep", start_date, end_date),
        "sleep_stages": get_fitbit_data("sleep/stages", start_date, end_date),

        # ⚖️ Kroppsvärden
        "weight": get_fitbit_data("body/weight", start_date, end_date),
        "fat": get_fitbit_data("body/fat", start_date, end_date),
        "bmi": get_fitbit_data("body/bmi", start_date, end_date),

        # 🥗 Kost
        "calories_in": get_fitbit_data("foods/log/caloriesIn", start_date, end_date),
        "nutrition": get_fitbit_data("foods/log", start_date, end_date),
        "water": get_fitbit_data("foods/log/water", start_date, end_date),

        # 👤 Profil
        "profile": get_fitbit_data("profile.json", start_date, end_date),
    }
