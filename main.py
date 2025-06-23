from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
import os
import base64
from datetime import date, timedelta
from dotenv import load_dotenv

# Ladda miljövariabler
load_dotenv()

app = FastAPI()

# GPT-plugin-krav: publicera statiskt innehåll
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

# Miljövariabler
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
        return {"message": "❌ Kunde inte tolka svaret från Fitbit.", "error": str(e), "raw_response": response.text}

    if "access_token" in token_data:
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        return {"message": "✅ Token mottagen och sparad!", "token_data": token_data}
    else:
        return {"message": "⚠️ Tokenutbyte misslyckades.", "token_data": token_data}

def refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None

    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)

    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    test = requests.get("https://api.fitbit.com/1/user/-/profile.json", headers=headers)

    if test.status_code == 200:
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

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

@app.get("/data")
def get_combined_data(days: int = 1):
    today = date.today()
    start_date = (today - timedelta(days=days - 1)).isoformat()
    end_date = today.isoformat()

    steps = get_fitbit_data("activities/steps", start_date, end_date)
    calories = get_fitbit_data("activities/calories", start_date, end_date)
    sleep = get_fitbit_data("sleep", start_date, end_date)
    heart = get_fitbit_data("activities/heart", start_date, end_date)

    errors = []
    for key, val in [("steps", steps), ("calories", calories), ("sleep", sleep), ("heart", heart)]:
        if isinstance(val, dict) and "error" in val:
            errors.append(f"{key}: {val['error']}")

    if errors:
        return {"status": "error", "message": "Fel vid hämtning av data.", "details": errors}

    return {
        "status": "ok",
        "from": start_date,
        "to": end_date,
        "steps": steps,
        "calories": calories,
        "sleep": sleep,
        "heart": heart
    }

@app.get("/data/extended")
def get_extended_data(days: int = 1, target_date: str = None):
    if target_date:
        start_date = end_date = target_date
    else:
        today = date.today()
        start_date = (today - timedelta(days=days - 1)).isoformat()
        end_date = today.isoformat()

    def safe_get(resource):
        result = get_fitbit_data(resource, start_date, end_date)
        return result if result else {"error": f"Ingen data för {resource}"}

    return {
        "from": start_date,
        "to": end_date,
        "steps": safe_get("activities/steps"),
        "calories": safe_get("activities/calories"),
        "active_calories": safe_get("activities/activityCalories"),
        "activity_log": safe_get("activities"),
        "distance": safe_get("activities/distance"),
        "floors": safe_get("activities/floors"),
        "elevation": safe_get("activities/elevation"),
        "activity_levels": {
            "sedentary": safe_get("activities/minutesSedentary"),
            "lightly_active": safe_get("activities/minutesLightlyActive"),
            "fairly_active": safe_get("activities/minutesFairlyActive"),
            "very_active": safe_get("activities/minutesVeryActive"),
        },
        "heart": safe_get("activities/heart"),
        "spo2": safe_get("spo2"),
        "breathing_rate": safe_get("br"),
        "core_temp": safe_get("temp/core"),
        "skin_temp": safe_get("temp/skin"),
        "stress": safe_get("body/stressManagement"),
        "sleep": safe_get("sleep"),
        "sleep_stages": safe_get("sleep/stages"),
        "weight": safe_get("body/weight"),
        "fat": safe_get("body/fat"),
        "bmi": safe_get("body/bmi"),
        "calories_in": safe_get("foods/log/caloriesIn"),
        "nutrition": safe_get("foods/log"),
        "water": safe_get("foods/log/water"),
        "profile": safe_get("profile.json")
    }
