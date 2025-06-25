from fastapi import FastAPI, HTTPException              # 🆕  lade till HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests, json, os, base64, time
from datetime import date, timedelta
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------
load_dotenv()

app = FastAPI()
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"              # 🆕  ny profilfil

# ---------------------------------------------------------------------
# Helper-funktioner för användarprofil  🆕
# ---------------------------------------------------------------------
def load_profile() -> dict:
    """Läs profil från disk (tom dict om filen inte finns)."""
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print("⚠️  Kunde inte läsa profil:", e)
        return {}

def save_profile(profile: dict):
    """Spara profil till disk."""
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("❌ Kunde inte spara profil:", e)
        raise

# ---------------------------------------------------------------------
# Routes – UI + profil  🆕
# ---------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT är igång! 🚀</h1>"
        "<p><a href='/authorize'>Logga in med Fitbit</a></p>"
        "<p><a href='/docs'>Swagger-dokumentation</a></p>"
    )

@app.get("/user_profile")
def get_user_profile():
    """Hämta lagrad användarprofil (kan vara tom)."""
    return load_profile()

@app.post("/user_profile")
def set_user_profile(profile: dict):
    """Spara eller uppdatera hela användarprofilen."""
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Body måste vara ett JSON-objekt.")
    save_profile(profile)
    return {"message": "✅ Profil sparad!", "profile": profile}

# ---------------------------------------------------------------------
# DINA BEFINTLIGA FITBIT-ENDPOINTS (oförändrade)
# ---------------------------------------------------------------------
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
    token_url   = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }
    data = {
        "client_id":   FITBIT_CLIENT_ID,
        "grant_type":  "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code":         code,
    }

    response = requests.post(token_url, headers=headers, data=data)
    try:
        token_data = response.json()
    except Exception as e:
        return {
            "message": "❌ Kunde inte tolka svaret från Fitbit.",
            "error":   str(e),
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
    token_url   = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type":  "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type":    "refresh_token",
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
    headers      = {"Authorization": f"Bearer {access_token}"}
    url          = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"

    time.sleep(1)  # 💡 Undvik rate limiting

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "5"))
            print(f"⚠️ Rate limit – väntar {retry_after} sekunder...")
            time.sleep(retry_after)
            response = requests.get(url, headers=headers)

        response.raise_for_status()
        data = response.json()
        if not data:
            return {"data": {}, "message": "Inget registrerat för denna dag"}
        return {"data": data}
    except Exception as e:
        print(f"⚠️ Fel vid hämtning av {resource_path}: {e}")
        return {"data": {}, "error": str(e)}

def get_activity_logs(date_str):
    token_data = refresh_token_if_needed()
    if not token_data:
        return []

    access_token = token_data.get("access_token")
    headers      = {"Authorization": f"Bearer {access_token}"}
    url          = f"https://api.fitbit.com/1/user/-/activities/list.json?beforeDate={date_str}&sort=desc&limit=10&offset=0"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("activities", [])
    except Exception as e:
        print(f"⚠️ Fel vid hämtning av loggade aktiviteter: {e}")
        return []

# ------------- Smala endpoints ----------------------------------------
@app.get("/data/steps")
def get_steps(date: str):
    return get_fitbit_data("activities/steps", date, date)

@app.get("/data/sleep")
def get_sleep(date: str):
    return get_fitbit_data("sleep", date, date)

@app.get("/data/heart")
def get_heart(date: str):
    return get_fitbit_data("activities/heart", date, date)

@app.get("/data/calories")
def get_calories(date: str):
    return get_fitbit_data("activities/calories", date, date)

# ------------- Sammanfattning -----------------------------------------
@app.get("/data")
def get_summary(days: int = 1):
    today      = date.today()
    start_date = (today - timedelta(days=days - 1)).isoformat()
    end_date   = today.isoformat()

    return {
        "from":      start_date,
        "to":        end_date,
        "steps":     get_fitbit_data("activities/steps",   start_date, end_date),
        "calories":  get_fitbit_data("activities/calories",start_date, end_date),
        "sleep":     get_fitbit_data("sleep",              start_date, end_date),
        "heart":     get_fitbit_data("activities/heart",   start_date, end_date),
    }

# ------------- Extended ------------------------------------------------
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: str = None):
    if target_date:
        start_date = end_date = target_date
    else:
        today      = date.today()
        start_date = (today - timedelta(days=days - 1)).isoformat()
        end_date   = today.isoformat()

    return {
        "from":          start_date,
        "to":            end_date,
        "steps":         get_fitbit_data("activities/steps",   start_date, end_date),
        "calories":      get_fitbit_data("activities/calories",start_date, end_date),
        "sleep":         get_fitbit_data("sleep",              start_date, end_date),
        "heart":         get_fitbit_data("activities/heart",   start_date, end_date),
        "weight":        get_fitbit_data("body/log/weight",    start_date, end_date),
        "activity_logs": get_activity_logs(end_date),
        "hrv":           get_fitbit_data("hrv",                start_date, end_date),
    }
