# -----------------------------------------------------------
# 🚀 Imports
# -----------------------------------------------------------
from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import requests, json, os, base64, time
from datetime import date as dt_date, timedelta, datetime as dt
from cachetools import TTLCache, cached
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore

from dateutil import parser as dt_parse   # för robust tids-parse

# -----------------------------------------------------------
# 🚀 Init & env
# -----------------------------------------------------------
load_dotenv()

app = FastAPI(title="FitGPT API")
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"

# -----------------------------------------------------------
# 📁 Lokal användarprofil
# -----------------------------------------------------------
PROFILE_FILE = "user_profile.json"

def load_profile() -> dict:
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def save_profile(profile: dict):
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

# -----------------------------------------------------------
# 🔗 Firestore-klient
# -----------------------------------------------------------
cred_info = json.loads(os.getenv("FIREBASE_CRED_JSON", "{}"))
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))

# -----------------------------------------------------------
# 🎯 Datamodeller
# -----------------------------------------------------------
class MealLog(BaseModel):
    date: str
    meal: str
    items: str
    estimated_calories: Optional[int] = None

class WorkoutLog(BaseModel):
    date: str
    workout_type: str
    details: str
    # äldre klienter kan skicka "type"
    type: Optional[str] = Field(None, alias="workout_type")
    class Config:
        allow_population_by_field_name = True

# -----------------------------------------------------------
# 🔒 Valfri API-nyckel
# -----------------------------------------------------------
def verify_auth(request: Request):
    required = os.getenv("API_KEY")
    if not required:
        return
    token = request.headers.get("authorization")
    if token != f"Bearer {required}":
        raise HTTPException(status_code=401, detail="Missing/invalid token")

# -----------------------------------------------------------
# 🌐 UI & profil
# -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT-API 🚀</h1>"
        "<p><a href='/authorize'>Logga in med Fitbit</a></p>"
        "<p><a href='/docs'>Swagger</a></p>"
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

# -----------------------------------------------------------
# 🔥 Firestore – måltider & pass
# -----------------------------------------------------------
@app.post("/log/meal", dependencies=[Depends(verify_auth)])
def post_meal(entry: MealLog = Body(...)):
    doc_id = f"{entry.date}-{entry.meal.lower()}"
    db.collection("meals").document(doc_id).set(entry.dict(exclude_none=True))
    return {"id": doc_id, "status": "stored", "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.get("/log/meal")
def get_meals(date: str):
    docs = db.collection("meals").where("date", "==", date).stream()
    return [d.to_dict() for d in docs]

@app.post("/log/workout", dependencies=[Depends(verify_auth)])
def post_workout(entry: WorkoutLog = Body(...)):
    doc_ref = db.collection("workouts").add(entry.dict(by_alias=True, exclude_none=True))[1]
    return {"id": doc_ref.id, "status": "stored", "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.get("/log/workout")
def get_workouts(date: str):
    docs = db.collection("workouts").where("date", "==", date).stream()
    return [d.to_dict() for d in docs]

# -----------------------------------------------------------
# 💾 Fitbit OAuth
# -----------------------------------------------------------
@app.get("/authorize")
def authorize():
    url = (
        "https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=activity%20heartrate%20sleep%20nutrition%20weight%20location%20profile"
    )
    return RedirectResponse(url)

@app.get("/callback")
def callback(code: str):
    token_url   = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        token_url,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "client_id":  FITBIT_CLIENT_ID,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
    )
    data = resp.json()
    if "access_token" in data:
        data["_saved_at"] = time.time()
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f)
        return {"message": "✅ Token sparad", "token_data": data}
    raise HTTPException(status_code=400, detail=data)

# -----------------------------------------------------------
# Fitbit helpers
# -----------------------------------------------------------
def _load_token() -> Optional[Dict]:
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)

def refresh_token_if_needed() -> Optional[Dict]:
    token_data = _load_token()
    if not token_data:
        return None
    # giltig ≥ 60 s?
    if time.time() < token_data.get("_saved_at", 0) + token_data.get("expires_in", 28800) - 60:
        return token_data

    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        },
    )
    if resp.status_code == 200:
        new_token = resp.json()
        new_token["_saved_at"] = time.time()
        with open(TOKEN_FILE, "w") as f:
            json.dump(new_token, f)
        return new_token
    return None

def fitbit_request(url: str, *, params: dict | None = None) -> dict | None:
    token = refresh_token_if_needed()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(url, headers=headers, params=params or {})
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.get(url, headers=headers, params=params or {})
    try:
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

# ---------- Fitbit datasets -------------------------------
def get_fitbit_data(resource_path: str, start_date: str, end_date: str):
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"
    data = fitbit_request(url)
    return {"data": data or {}}

def get_fitbit_workouts(date_str: str) -> List[dict]:
    """
    Hämtar *tracker-loggade* aktiviteter som startar exakt date_str (YYYY-MM-DD).
    """
    url = "https://api.fitbit.com/1/user/-/activities/list.json"
    params = {"afterDate": date_str, "sort": "asc", "limit": 20, "offset": 0}
    raw = fitbit_request(url, params=params) or {}
    acts = raw.get("activities", [])
    return [a for a in acts if a.get("startTime", "").startswith(date_str)]

# ---------- Slå ihop manuella + Fitbit-pass ---------------
def _combine_workouts(date_str: str) -> List[dict]:
    manual = [{**w, "source": "manual"} for w in get_workouts(date_str)]
    auto   = [{**a, "source": "fitbit"} for a in get_fitbit_workouts(date_str)]

    def is_dup(a_item: dict) -> bool:
        a_ts = dt_parse.parse(a_item["startTime"])
        for m in manual:
            m_ts_str = m.get("startTime")
            if not m_ts_str:
                continue
            m_ts = dt_parse.parse(m_ts_str)
            if abs((a_ts - m_ts).total_seconds()) < 1800:
                return True
        return False

    auto_clean = [a for a in auto if not is_dup(a)]
    return manual + auto_clean

# -----------------------------------------------------------
# 📦 Daily summary
# -----------------------------------------------------------
_cache = TTLCache(maxsize=64, ttl=300)          # 5 min historik

def _build_daily_summary(date_str: str, *, bypass_cache: bool = False):
    return {
        "date":     date_str,
        "fitbit":   get_extended(target_date=date_str),
        "meals":    get_meals(date_str),
        "workouts": _combine_workouts(date_str),
    }

_cached = cached(_cache)(_build_daily_summary)

@app.get("/daily-summary")
def daily_summary(target_date: Optional[str] = None, fresh: bool = False):
    if not target_date:
        target_date = dt_date.today().isoformat()

    is_today = (target_date == dt_date.today().isoformat())

    if fresh or is_today:
        _cache.pop(target_date, None)          # kasta ev. gammal
        return _build_daily_summary(target_date)

    return _cached(target_date)

# ---------- Smala Fitbit endpoints -------------------------
@app.get("/data/steps")
def steps(date: str):
    return get_fitbit_data("activities/steps", date, date)

@app.get("/data/sleep")
def sleep(date: str):
    return get_fitbit_data("sleep", date, date)

@app.get("/data/heart")
def heart(date: str):
    return get_fitbit_data("activities/heart", date, date)

@app.get("/data/calories")
def calories(date: str):
    return get_fitbit_data("activities/calories", date, date)

# ---------- Extended datasets ------------------------------
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: Optional[str] = None):
    if target_date:
        start = end = target_date
    else:
        today = dt_date.today()
        start = (today - timedelta(days=days - 1)).isoformat()
        end   = today.isoformat()
    return {
        "from":   start,
        "to":     end,
        "steps":    get_fitbit_data("activities/steps",    start, end),
        "calories": get_fitbit_data("activities/calories", start, end),
        "sleep":    get_fitbit_data("sleep",               start, end),
        "heart":    get_fitbit_data("activities/heart",    start, end),
        "weight":   get_fitbit_data("body/log/weight",     start, end),
        "hrv":      get_fitbit_data("hrv",                 start, end),
    }

# ---------- Extended FULL ----------------------------------
@app.get("/data/extended/full")
def get_extended_full(days: int = 1, fresh: bool = False):
    if days < 1:
        raise HTTPException(status_code=400, detail="days måste vara ≥ 1")

    today  = dt_date.today()
    start  = today - timedelta(days=days - 1)
    dates  = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    out: Dict[str, dict] = {}
    for d in dates:
        fitbit_d = get_extended(target_date=d)
        meals    = get_meals(d)
        workouts = _combine_workouts(d)

        if fresh and d == today.isoformat():
            _cache.pop(d, None)
            fitbit_d = get_extended(target_date=d)
            meals    = get_meals(d)
            workouts = _combine_workouts(d)

        fitbit_d["meals"]    = meals
        fitbit_d["workouts"] = workouts
        out[d] = fitbit_d

    return {"from": dates[0], "to": dates[-1], "days": out}
