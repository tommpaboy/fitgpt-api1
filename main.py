from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from typing import List, Optional
import requests, json, os, base64, time
from datetime import date, timedelta
from functools import lru_cache
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# Init & env
# ---------------------------------------------------------------------
load_dotenv()

app = FastAPI()
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

# Fitbit-inställningar --------------------------------------------------
FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"

# Användarprofil (lokal JSON) ------------------------------------------
PROFILE_FILE = "user_profile.json"

def load_profile() -> dict:
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print("⚠️  Kunde inte läsa profil:", e)
        return {}

def save_profile(profile: dict):
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("❌ Kunde inte spara profil:", e)
        raise

# ---------------------------------------------------------------------
# 🔗 Firestore-klient
# ---------------------------------------------------------------------
from google.oauth2 import service_account
from google.cloud import firestore

FIREBASE_CRED_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
if not FIREBASE_CRED_JSON:
    raise RuntimeError("Miljövariabeln FIREBASE_CREDENTIALS_JSON saknas!")

cred_info = json.loads(FIREBASE_CRED_JSON)
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))

# ---------------------------------------------------------------------
# 🎯 Datamodeller för loggning
# ---------------------------------------------------------------------
class MealLog(BaseModel):
    date: str
    meal: str
    items: str
    estimated_calories: Optional[int] = None

class WorkoutLog(BaseModel):
    date: str
    type: str
    details: str

# ---------------------------------------------------------------------
# Routes – UI och profil
# ---------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT-API 🚀</h1>"
        "<p><a href='/authorize'>Logga in med Fitbit</a></p>"
        "<p><a href='/docs'>Swagger-dokumentation</a></p>"
    )

@app.get("/user_profile")
def get_user_profile():
    return load_profile()

@app.post("/user_profile")
def set_user_profile(profile: dict):
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Body måste vara ett JSON-objekt.")
    save_profile(profile)
    return {"message": "✅ Profil sparad!", "profile": profile}

# ---------------------------------------------------------------------
# 🔥 Firestore-loggar – måltider
# ---------------------------------------------------------------------
@app.post("/log/meal")
def log_meals(entries: List[MealLog]):
    saved = []
    for entry in entries:
        doc_id = f"{entry.date}-{entry.meal.lower()}"
        db.collection("meals").document(doc_id).set(entry.dict())
        saved.append(entry.dict())
    return {"status": "OK", "saved": saved}

@app.get("/log/meal")
def get_meals(date: str):
    docs = db.collection("meals").where("date", "==", date).stream()
    return [d.to_dict() for d in docs]

# ---------------------------------------------------------------------
# 🔥 Firestore-loggar – träningspass
# ---------------------------------------------------------------------
@app.post("/log/workout")
def log_workouts(entries: List[WorkoutLog]):
    saved = []
    for entry in entries:
        db.collection("workouts").add(entry.dict())  # auto-id
        saved.append(entry.dict())
    return {"status": "OK", "saved": saved}

@app.get("/log/workout")
def get_workouts(date: str):
    docs = db.collection("workouts").where("date", "==", date).stream()
    return [d.to_dict() for d in docs]

# ---------------------------------------------------------------------
# 💾 FITBIT-endpoints (oförändrade)
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
    token_data = response.json()
    if "access_token" in token_data:
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        return {"message": "✅ Token mottagen och sparad!", "token_data": token_data}
    return {"message": "⚠️ Något gick fel vid tokenutbyte.", "token_data": token_data}

# ---------------------------------------------------------------------
# Token-hantering & Fitbit-helpers
# ---------------------------------------------------------------------
def refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    if requests.get("https://api.fitbit.com/1/user/-/profile.json", headers=headers).status_code == 200:
        return token_data
    # refresh
    data = {
        "grant_type":    "refresh_token",
        "refresh_token": token_data["refresh_token"],
    }
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    response = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers={"Authorization": f"Basic {auth_header}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=data,
    )
    if response.status_code == 200:
        new_token_data = response.json()
        with open(TOKEN_FILE, "w") as f:
            json.dump(new_token_data, f)
        return new_token_data
    print("❌ Kunde inte förnya token:", response.text)
    return None

def get_fitbit_data(resource_path, start_date, end_date):
    token_data = refresh_token_if_needed()
    if not token_data:
        return {"error": "Ingen giltig token hittades."}
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"
    time.sleep(1)  # enkel throttling
    resp = requests.get(url, headers=headers)
    if resp.status_code == 429:               # rate-limit
        wait = int(resp.headers.get("Retry-After", 5))
        time.sleep(wait)
        resp = requests.get(url, headers=headers)
    try:
        resp.raise_for_status()
        return {"data": resp.json() or {}}
    except Exception as e:
        return {"error": str(e), "data": {}}

def get_activity_logs(date_str):
    token_data = refresh_token_if_needed()
    if not token_data:
        return []
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    url = f"https://api.fitbit.com/1/user/-/activities/list.json?beforeDate={date_str}&sort=desc&limit=10&offset=0"
    try:
        return requests.get(url, headers=headers).json().get("activities", [])
    except Exception:
        return []

# ---------------------------------------------------------------------
# 📦 NY: Daily summary (Fitbit + Firestore) – 1 anrop för GPT
# ---------------------------------------------------------------------
@lru_cache(maxsize=64)
def _build_daily_summary(target_date: str):
    fitbit   = get_extended(target_date=target_date)
    meals    = get_meals(target_date)
    workouts = get_workouts(target_date)
    return {
        "date": target_date,
        "fitbit": fitbit,
        "meals": meals,
        "workouts": workouts,
    }

@app.get("/daily-summary")
def daily_summary(date: str = None):
    """Samlad dagsdata; resultatet cacha 60 sek per datum."""
    if not date:
        date = date.today().isoformat()
    return _build_daily_summary(date)

# ---------- Smala endpoints (behålls för bakåtkompatibilitet) ---------
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

# ---------- Sammanfattning -------------------------------------------
@app.get("/data")
def get_summary(days: int = 1):
    today      = date.today()
    start_date = (today - timedelta(days=days - 1)).isoformat()
    end_date   = today.isoformat()
    return {
        "from":   start_date,
        "to":     end_date,
        "steps":  get_fitbit_data("activities/steps",    start_date, end_date),
        "calories": get_fitbit_data("activities/calories", start_date, end_date),
        "sleep":  get_fitbit_data("sleep",               start_date, end_date),
        "heart":  get_fitbit_data("activities/heart",    start_date, end_date),
    }

# ---------- Extended --------------------------------------------------
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: str = None):
    if target_date:
        start_date = end_date = target_date
    else:
        today      = date.today()
        start_date = (today - timedelta(days=days - 1)).isoformat()
        end_date   = today.isoformat()

    return {
        "from":   start_date,
        "to":     end_date,
        "steps":  get_fitbit_data("activities/steps",    start_date, end_date),
        "calories": get_fitbit_data("activities/calories", start_date, end_date),
        "sleep":  get_fitbit_data("sleep",               start_date, end_date),
        "heart":  get_fitbit_data("activities/heart",    start_date, end_date),
        "weight": get_fitbit_data("body/log/weight",     start_date, end_date),
        "activity_logs": get_activity_logs(end_date),
        "hrv":    get_fitbit_data("hrv",                 start_date, end_date),
    }
