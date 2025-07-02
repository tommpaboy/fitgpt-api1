# -----------------------------------------------------------
# 🏋️‍♂️ FitGPT – main.py  (Svensk tidszon + full funktion)
# -----------------------------------------------------------
# Kombinerar Fitbit-data och manuella måltider/träningspass.
# -----------------------------------------------------------

# 🚀 Imports
# -----------------------------------------------------------
from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field
from typing import Optional, List, Tuple
import requests, json, os, base64, time, re
from datetime import datetime as dt, timedelta, date as dt_date
from zoneinfo import ZoneInfo          # ← NYTT
from cachetools import TTLCache, cached
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore

# -----------------------------------------------------------
# 🌱 Init & miljövariabler
# -----------------------------------------------------------
load_dotenv()
SE_TZ = ZoneInfo("Europe/Stockholm")   # ← NYTT

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"

# -----------------------------------------------------------
# ⚙️ FastAPI-instans
# -----------------------------------------------------------
app = FastAPI(title="FitGPT API")
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------
# 🔗 Firestore-klient
# -----------------------------------------------------------
cred_info = json.loads(os.getenv("FIREBASE_CRED_JSON", "{}"))
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))

# -----------------------------------------------------------
# 🎯 Pydantic-modeller
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
    start_time: Optional[str] = Field(
        None, alias="startTime",
        description="ISO-tid (YYYY-MM-DDTHH:MM:SS). Tomt ⇒ FitGPT gissar."
    )
    type: Optional[str] = Field(None, alias="workout_type")  # bakåtkomp.

    class Config:
        allow_population_by_field_name = True

# -----------------------------------------------------------
# 🔒 Valfri API-nyckel
# -----------------------------------------------------------
def verify_auth(request: Request):
    required = os.getenv("API_KEY")      # tom = auth av
    if not required:
        return
    token = request.headers.get("authorization")
    if token != f"Bearer {required}":
        raise HTTPException(status_code=401, detail="Missing/invalid token")

# -----------------------------------------------------------
# 🏠 Mini-UI
# -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT-API 🚀</h1>"
        "<ul><li><a href='/authorize'>Logga in med Fitbit</a></li>"
        "<li><a href='/docs'>Swagger-dokumentation</a></li></ul>"
    )

# -----------------------------------------------------------
# 🔑 Fitbit OAuth
# -----------------------------------------------------------
@app.get("/authorize")
def authorize():
    url = (
        "https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=activity%20nutrition%20sleep%20heartrate%20weight%20location%20profile"
    )
    return RedirectResponse(url)


@app.get("/callback")
def callback(code: str):
    token_url   = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()

    resp = requests.post(
        token_url,
        headers={"Authorization": f"Basic {auth_header}",
                 "Content-Type": "application/x-www-form-urlencoded"},
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
# 📁 Användar-profil
# -----------------------------------------------------------
def _load_profile() -> dict:
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_profile(profile: dict):
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

@app.get("/user_profile")
def get_profile():
    return _load_profile()

@app.post("/user_profile")
def set_profile(profile: dict):
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Body måste vara ett JSON-objekt.")
    _save_profile(profile)
    return {"message": "✅ Sparat!", "profile": profile}

# -----------------------------------------------------------
# 🔥 Firestore – MÅLTIDER
# -----------------------------------------------------------
@app.post("/log/meal", dependencies=[Depends(verify_auth)])
def post_meal(entry: MealLog = Body(...)):
    doc_id = f"{entry.date}-{entry.meal.lower()}"
    db.collection("meals").document(doc_id).set(entry.dict(exclude_none=True))
    return {"id": doc_id, "status": "stored",
            "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.get("/log/meal")
def get_meals(date: str):
    docs = db.collection("meals").where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@app.put("/log/meal/{doc_id}", dependencies=[Depends(verify_auth)])
def put_meal(doc_id: str, entry: MealLog = Body(...)):
    db.collection("meals").document(doc_id).set(entry.dict(exclude_none=True))
    return {"id": doc_id, "status": "updated",
            "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.delete("/log/meal/{doc_id}", dependencies=[Depends(verify_auth)])
def del_meal(doc_id: str):
    db.collection("meals").document(doc_id).delete()
    return {"id": doc_id, "status": "deleted"}

# -----------------------------------------------------------
# 🕒 Hjälp­funktioner – duration & matchning
# -----------------------------------------------------------
def _extract_duration_min(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)\s*(?:min|\bmins?\b|\bm\b)", text.lower())
    return int(m.group(1)) if m else None


def _guess_auto_match(m_entry: dict, auto_logs: List[dict], used_idx: set) -> Tuple[Optional[int], float]:
    dur_m = _extract_duration_min(m_entry.get("details", "")) or None
    wt_m  = m_entry.get("workout_type", "").lower()

    best_idx, best_score = None, 0.0
    for idx, a in enumerate(auto_logs):
        if idx in used_idx:
            continue
        name = a.get("activityName", "").lower()
        score = 0.0

        if wt_m and wt_m in name:
            score += 0.6
        elif name and name in wt_m:
            score += 0.4

        dur_a = a.get("duration", 0) / 60000
        if dur_m:
            diff = abs(dur_a - dur_m) / max(dur_a, dur_m, 1)
            score += 0.4 if diff <= 0.05 else 0.2 if diff <= 0.15 else 0.0

        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx, best_score

# -----------------------------------------------------------
# 💾 Fitbit helpers
# -----------------------------------------------------------
def refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)

    exp_secs = token_data.get("expires_in", 28800)
    saved_at = token_data.get("_saved_at", 0)
    if time.time() < saved_at + exp_secs - 60:
        return token_data

    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers={"Authorization": f"Basic {auth_header}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token",
              "refresh_token": token_data["refresh_token"]},
    )
    if resp.status_code == 200:
        new_token = resp.json()
        new_token["_saved_at"] = time.time()
        with open(TOKEN_FILE, "w") as f:
            json.dump(new_token, f)
        return new_token
    return None


def get_fitbit_data(resource_path, start_date, end_date):
    token = refresh_token_if_needed()
    if not token:
        return {"error": "Ingen giltig token."}

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.get(url, headers=headers)
    try:
        resp.raise_for_status()
        return {"data": resp.json()}
    except Exception as e:
        return {"error": str(e), "data": {}}


def get_activity_logs(date_str: str) -> List[dict]:
    token = refresh_token_if_needed()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    url = ("https://api.fitbit.com/1/user/-/activities/list.json"
           f"?beforeDate={date_str}T23:59:59&sort=desc&limit=50&offset=0")
    try:
        raw = requests.get(url, headers=headers).json().get("activities", [])
        return [a for a in raw if a.get("originalStartTime", "").startswith(date_str)]
    except Exception:
        return []

# -----------------------------------------------------------
# 🔥 Firestore – TRÄNINGSPASS
# -----------------------------------------------------------
def _infer_start_time(entry: WorkoutLog) -> Tuple[Optional[str], bool]:
    auto_logs = get_activity_logs(entry.date)
    idx, conf = _guess_auto_match(entry.dict(exclude_none=True), auto_logs, set())
    if idx is not None and (conf >= 0.8 or (conf >= 0.6 and len(auto_logs) == 1)):
        start_iso = auto_logs[idx]["originalStartTime"][:-6]
        return start_iso, False
    return None, True

@app.post("/log/workout", dependencies=[Depends(verify_auth)])
def post_workout(entry: WorkoutLog = Body(...)):
    confirm = False
    if not entry.start_time:
        guessed, confirm = _infer_start_time(entry)
        if guessed:
            entry.start_time = guessed

    doc_ref = db.collection("workouts").add(entry.dict(by_alias=True, exclude_none=True))[1]
    return {"id": doc_ref.id, "status": "stored",
            "needs_confirmation": confirm,
            "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.get("/log/workout")
def get_workouts(date: str):
    docs = db.collection("workouts").where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@app.put("/log/workout/{doc_id}", dependencies=[Depends(verify_auth)])
def put_workout(doc_id: str, entry: WorkoutLog = Body(...)):
    db.collection("workouts").document(doc_id).set(entry.dict(by_alias=True, exclude_none=True))
    return {"id": doc_id, "status": "updated",
            "daily": _build_daily_summary(entry.date, bypass_cache=True)}

@app.delete("/log/workout/{doc_id}", dependencies=[Depends(verify_auth)])
def del_workout(doc_id: str):
    db.collection("workouts").document(doc_id).delete()
    return {"id": doc_id, "status": "deleted"}

# -----------------------------------------------------------
# 🧬 Slå ihop manuella + Fitbit-pass
# -----------------------------------------------------------
def _combine_workouts(date_str: str) -> List[dict]:
    manual_raw = get_workouts(date_str)
    manual = [{**w, "source": "manual"} for w in manual_raw]

    auto_raw = get_activity_logs(date_str)
    auto = [{**a, "source": "fitbit"} for a in auto_raw]

    merged, used_auto = [], set()

    # 1. Matcha manuella med start_time
    for m in manual:
        st = m.get("start_time") or m.get("startTime")
        if not st:
            continue
        try:
            m_ts = dt.fromisoformat(st)
        except Exception:
            merged.append(m); continue

        matched = False
        for idx, a in enumerate(auto):
            if idx in used_auto:
                continue
            try:
                a_ts = dt.fromisoformat(a["originalStartTime"][:-6])
            except Exception:
                continue
            if abs((a_ts - m_ts).total_seconds()) < 1800:
                used_auto.add(idx)
                merged.append({**a, **m, "source": "merged"})
                matched = True
                break
        if not matched:
            merged.append(m)

    # 2. Matcha manuella utan start_time via heuristik
    for m in [x for x in manual if not (x.get("start_time") or x.get("startTime"))]:
        idx, conf = _guess_auto_match(m, auto, used_auto)
        if idx is not None and conf >= 0.8:
            used_auto.add(idx)
            m["start_time"] = auto[idx]["originalStartTime"][:-6]
            merged.append({**auto[idx], **m, "source": "merged"})
        else:
            merged.append({**m, "needs_confirmation": True})

    # 3. Kvarvarande Fitbit-pass
    merged.extend([a for i, a in enumerate(auto) if i not in used_auto])
    return merged

# -----------------------------------------------------------
# 📦 Daily summary (cache 5 min)
# -----------------------------------------------------------
_cache = TTLCache(maxsize=64, ttl=60)

def _build_daily_summary(date_str: str, *, bypass_cache=False):
    return {
        "date": date_str,
        "fitbit": get_extended(target_date=date_str),
        "meals": get_meals(date_str),
        "workouts": _combine_workouts(date_str),
    }

_cached = cached(_cache)(_build_daily_summary)

@app.get("/daily-summary")
def daily_summary(target_date: Optional[str] = None, fresh: bool = False):
    if not target_date:
        target_date = dt.now(SE_TZ).date().isoformat()          # ← ÄNDRAD
    if fresh or target_date == dt.now(SE_TZ).date().isoformat():
        return _build_daily_summary(target_date)
    return _cached(target_date)

# -----------------------------------------------------------
# 🔄 Små Fitbit-proxy-endpoints
# -----------------------------------------------------------
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

# -----------------------------------------------------------
# 📈 Extended Fitbit-data
# -----------------------------------------------------------
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: Optional[str] = None):
    if target_date:
        start = end = target_date
    else:
        today = dt.now(SE_TZ).date()                           # ← ÄNDRAD
        start = (today - timedelta(days=days - 1)).isoformat()
        end = today.isoformat()
    return {
        "from": start, "to": end,
        "steps": get_fitbit_data("activities/steps", start, end),
        "calories": get_fitbit_data("activities/calories", start, end),
        "sleep": get_fitbit_data("sleep", start, end),
        "heart": get_fitbit_data("activities/heart", start, end),
        "weight": get_fitbit_data("body/log/weight", start, end),
        "hrv": get_fitbit_data("hrv", start, end),
    }

# -----------------------------------------------------------
# 🧩 Extended FULL (Fitbit + Firestore) – alltid färsk vid fresh=true
# -----------------------------------------------------------
@app.get("/data/extended/full")
def get_extended_full(days: int = 1, fresh: bool = False):
    """
    Returnerar:
      {
        "from": <ISO>,
        "to": <ISO>,
        "days": {
          "<YYYY-MM-DD>": {
            "date": ...,
            "fitbit": {...},
            "meals": [...],
            "workouts": [...]
          }, ...
        }
      }
    • days ≥ 1
    • fresh=true → hoppa cache för samtliga dagar
    """
    if days < 1:
        raise HTTPException(status_code=400, detail="days måste vara ≥ 1")

    today      = dt.now(SE_TZ).date()
    start_date = today - timedelta(days=days - 1)
    dates      = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    out = {"from": dates[0], "to": dates[-1], "days": {}}
    for d in dates:
        out["days"][d] = _build_daily_summary(d, bypass_cache=fresh)
    return out
