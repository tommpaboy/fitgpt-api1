"""
🏋️‍♂️ FitGPT – main.py (rev 2025-07-03)
────────────────────────────────────────
• Primär endpoint: /data/daily-summary
• Svensk tidszon (Europe/Stockholm)
• TTL-cache (endast EXAKT kcal_out hamnar i cache)
• Fitbit + Firestore i ett enda JSON-svar
"""

from __future__ import annotations

# ────────────────  Standard + 3P  ────────────────
import os, json, re, time, base64, requests
from datetime import datetime, timedelta, date as dt_date
from typing import Optional, List, Tuple, Dict, Any, Set

from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo
from cachetools import TTLCache
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore  # pip install google-cloud-firestore

# ────────────────  Init  ────────────────
load_dotenv()
SE_TZ = ZoneInfo("Europe/Stockholm")

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"
API_KEY_REQUIRED     = os.getenv("API_KEY")  # valfritt

# ────────────────  FastAPI  ────────────────
app = FastAPI(title="FitGPT API")

app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ────────────────  Firestore  ────────────────
cred_info_raw = os.getenv("FIREBASE_CRED_JSON", "{}")
cred_info: Dict[str, Any] = json.loads(cred_info_raw)
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))
MEAL_COL    = db.collection("meals")
WORKOUT_COL = db.collection("workouts")

# ────────────────  Pydantic modeller  ────────────────
class MealLog(BaseModel):
    date: str       # YYYY-MM-DD
    meal: str
    items: str
    estimated_calories: Optional[int] = None


class WorkoutLog(BaseModel):
    date: str  # YYYY-MM-DD
    type: str  # Typ av pass, t.ex. Styrkepass eller Badminton
    details: str
    start_time: Optional[str] = Field(
        None,
        alias="startTime",
        description="ISO-tid (YYYY-MM-DDTHH:MM:SS). Tomt ⇒ FitGPT gissar.",
    )

    class Config:
        allow_population_by_field_name = True

# ────────────────  Auth helper  ────────────────
def verify_auth(request: Request):
    if not API_KEY_REQUIRED:
        return
    token = request.headers.get("authorization")
    if token != f"Bearer {API_KEY_REQUIRED}":
        raise HTTPException(status_code=401, detail="Missing or invalid token")

# ────────────────  Mini-UI  ────────────────
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT-API 🚀</h1>"
        "<ul>"
        "<li><a href='/authorize'>Logga in med Fitbit</a></li>"
        "<li><a href='/docs'>Swagger-dokumentation</a></li>"
        "</ul>"
    )

# ────────────────  Fitbit OAuth  ────────────────
@app.get("/authorize")
def authorize():
    scope = "activity nutrition sleep heartrate weight location profile"
    url = (
        "https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope.replace(' ', '%20')}"
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
        with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return {"message": "✅ Token sparad", "token_data": data}
    raise HTTPException(status_code=400, detail=data)

# ────────────────  Tids-utility  ────────────────
def _today_se() -> dt_date:
    return datetime.now(SE_TZ).date()

# ────────────────  Användar-profil (lokal JSON)  ────────────────
def _load_profile() -> Dict[str, Any]:
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        with open(PROFILE_FILE, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}

def _save_profile(profile: Dict[str, Any]):
    with open(PROFILE_FILE, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2)

@app.get("/user_profile")
def get_profile():
    return _load_profile()

@app.post("/user_profile")
def set_profile(profile: Dict[str, Any]):
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Body måste vara ett JSON-objekt.")
    _save_profile(profile)
    return {"message": "✅ Sparat!", "profile": profile}

# ────────────────  Meal-endpoints  ────────────────
@app.post("/log/meal", dependencies=[Depends(verify_auth)])
def post_meal(entry: MealLog = Body(...)):
    doc_id = f"{entry.date}-{entry.meal.lower()}"
    MEAL_COL.document(doc_id).set(entry.dict(exclude_none=True))
    return {
        "id": doc_id,
        "status": "stored",
        "daily": _get_daily_summary(entry.date, force_fresh=True),
    }

@app.get("/log/meal")
def get_meals(date: str):
    docs = MEAL_COL.where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@app.put("/log/meal/{doc_id}", dependencies=[Depends(verify_auth)])
def put_meal(doc_id: str, entry: MealLog = Body(...)):
    MEAL_COL.document(doc_id).set(entry.dict(exclude_none=True))
    return {
        "id": doc_id,
        "status": "updated",
        "daily": _get_daily_summary(entry.date, force_fresh=True),
    }

@app.delete("/log/meal/{doc_id}", dependencies=[Depends(verify_auth)])
def del_meal(doc_id: str):
    MEAL_COL.document(doc_id).delete()
    return {"id": doc_id, "status": "deleted"}

# ────────────────  Workout-heuristik-helpers  ────────────────
def _extract_duration_min(text: str) -> Optional[int]:
    m = re.search(r"(\\d+)\\s*(?:min|\\bmins?\\b|\\bm\\b)", text.lower()) if text else None
    return int(m.group(1)) if m else None

def _guess_auto_match(m_entry: Dict[str, Any], auto_logs: List[Dict[str, Any]], used: Set[int]) -> Tuple[Optional[int], float]:
    dur_m = _extract_duration_min(m_entry.get("details", "")) or None
    wt_m  = m_entry.get("workout_type", "").lower()
    best_idx, best_score = None, 0.0
    for idx, a in enumerate(auto_logs):
        if idx in used:
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

# ────────────────  Fitbit helpers  ────────────────
def _fitbit_auth_header() -> Dict[str, str]:
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {auth_header}", "Content-Type": "application/x-www-form-urlencoded"}

def _read_token() -> Dict[str, Any]:
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        return json.load(fh)

def _write_token(data: Dict[str, Any]):
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

def _refresh_token_if_needed() -> Optional[Dict[str, Any]]:
    if not os.path.exists(TOKEN_FILE):
        return None
    token_data = _read_token()
    # giltig ännu?
    if time.time() < token_data.get("_saved_at", 0) + token_data.get("expires_in", 28800) - 60:
        return token_data
    # refresh
    resp = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers=_fitbit_auth_header(),
        data={"grant_type": "refresh_token", "refresh_token": token_data["refresh_token"]},
    )
    if resp.status_code == 200:
        new_token = resp.json()
        new_token["_saved_at"] = time.time()
        _write_token(new_token)
        return new_token
    return None

def _fitbit_get(resource_path: str, start: str, end: str) -> Dict[str, Any]:
    token = _refresh_token_if_needed()
    if not token:
        return {"error": "Ingen giltig token."}
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start}/{end}.json"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.get(url, headers=headers)
    try:
        resp.raise_for_status()
        return {"data": resp.json()}
    except Exception as e:
        return {"error": str(e), "data": {}}

def _fitbit_activity_logs(date_str: str) -> List[Dict[str, Any]]:
    token = _refresh_token_if_needed()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    url = (
        "https://api.fitbit.com/1/user/-/activities/list.json"
        f"?beforeDate={date_str}T23:59:59&sort=desc&limit=50&offset=0"
    )
    try:
        raw = requests.get(url, headers=headers).json().get("activities", [])
        return [a for a in raw if a.get("originalStartTime", "").startswith(date_str)]
    except Exception:
        return []

# ────────────────  Workout-CRUD  ────────────────
def _infer_start_time(entry: WorkoutLog) -> Tuple[Optional[str], bool]:
    auto_logs = _fitbit_activity_logs(entry.date)
    idx, conf = _guess_auto_match(entry.dict(by_alias=True, exclude_none=True), auto_logs, set())
    if idx is not None and (conf >= 0.8 or (conf >= 0.6 and len(auto_logs) == 1)):
        return auto_logs[idx]["originalStartTime"][:-6], False
    return None, True

@app.post("/log/workout", dependencies=[Depends(verify_auth)])
def post_workout(entry: WorkoutLog = Body(...)):
    confirm = False
    if not entry.start_time:
        guessed, confirm = _infer_start_time(entry)
        if guessed:
            entry.start_time = guessed
    doc_ref = WORKOUT_COL.add(entry.dict(by_alias=True, exclude_none=True))[1]
    return {
        "id": doc_ref.id,
        "status": "stored",
        "needs_confirmation": confirm,
        "daily": _get_daily_summary(entry.date, force_fresh=True),
    }

@app.get("/log/workout")
def get_workouts(date: str):
    docs = WORKOUT_COL.where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@app.put("/log/workout/{doc_id}", dependencies=[Depends(verify_auth)])
def put_workout(doc_id: str, entry: WorkoutLog = Body(...)):
    WORKOUT_COL.document(doc_id).set(entry.dict(by_alias=True, exclude_none=True))
    return {
        "id": doc_id,
        "status": "updated",
        "daily": _get_daily_summary(entry.date, force_fresh=True),
    }

@app.delete("/log/workout/{doc_id}", dependencies=[Depends(verify_auth)])
def del_workout(doc_id: str):
    WORKOUT_COL.document(doc_id).delete()
    return {"id": doc_id, "status": "deleted"}

# ────────────────  Merge manuella + Fitbit-pass  ────────────────
def _combine_workouts(date_str: str) -> List[Dict[str, Any]]:
    manual = [{**w, "source": "manual"} for w in get_workouts(date_str)]
    auto   = [{**a, "source": "fitbit"} for a in _fitbit_activity_logs(date_str)]
    merged, used = [], set()

    # match manuella med start_time
    for m in manual:
        st = m.get("start_time") or m.get("startTime")
        if not st:
            continue
        try:
            m_ts = datetime.fromisoformat(st)
        except Exception:
            merged.append(m); continue
        matched = False
        for idx, a in enumerate(auto):
            if idx in used:
                continue
            try:
                a_ts = datetime.fromisoformat(a["originalStartTime"][:-6])
            except Exception:
                continue
            if abs((a_ts - m_ts).total_seconds()) < 1800:
                used.add(idx)
                merged.append({**a, **m, "source": "merged"}); matched = True; break
        if not matched:
            merged.append(m)

    # manuella utan start_time
    for m in [x for x in manual if not (x.get("start_time") or x.get("startTime"))]:
        idx, conf = _guess_auto_match(m, auto, used)
        if idx is not None and conf >= 0.8:
            used.add(idx)
            m["start_time"] = auto[idx]["originalStartTime"][:-6]
            merged.append({**auto[idx], **m, "source": "merged"})
        else:
            merged.append({**m, "needs_confirmation": True})

    merged.extend([a for i, a in enumerate(auto) if i not in used])
    return merged

# ────────────────  Daily summary helpers  ────────────────
CACHE = TTLCache(maxsize=64, ttl=60)  # 1 min

def _sum_cals(meals: List[Dict[str, Any]]) -> int:
    return sum(m.get("estimated_calories", 0) or 0 for m in meals)

def _extract_sleep(blob: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        nights = blob.get("data", {}).get("sleep", [])
        if not nights:
            return None
        total_ms = sum(n["duration"] for n in nights)
        return {
            "minutes": total_ms // 60000,
            "efficiency": round(sum(n.get("efficiency", 0) for n in nights) / len(nights)),
        }
    except Exception:
        return None

def _extract_hrv(blob: Dict[str, Any]) -> Optional[int]:
    try:
        series = blob.get("data", {}).get("hrv", [])
        return int(series[0]["value"]["rmssd"]) if series else None
    except Exception:
        return None

def _extract_kcal_out(blob: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    try:
        acts = blob.get("activities-calories") or blob["data"]["activities-calories"]
        return int(acts[0]["value"]), False
    except Exception:
        return None, True

# ────────────────  Bulk Fitbit  ────────────────
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: Optional[str] = None):
    if target_date:
        start = end = target_date
    else:
        today  = _today_se()
        start, end = (today - timedelta(days=days - 1)).isoformat(), today.isoformat()
    return {
        "from": start, "to": end,
        "steps":    _fitbit_get("activities/steps", start, end),
        "calories": _fitbit_get("activities/calories", start, end),
        "sleep":    _fitbit_get("sleep", start, end),
        "heart":    _fitbit_get("activities/heart", start, end),
        "weight":   _fitbit_get("body/log/weight", start, end),
        "hrv":      _fitbit_get("hrv", start, end),
    }

# ────────────────  Daily summary core  ────────────────
def _build_daily_summary(date_str: str) -> Dict[str, Any]:
    meals    = get_meals(date_str)
    workouts = _combine_workouts(date_str)
    fb       = get_extended(target_date=date_str)

    kcal_out, guessed = _extract_kcal_out(fb.get("calories", {}))
    return {
        "date":        date_str,
        "kcal_in":     _sum_cals(meals),
        "kcal_out":    None if guessed else kcal_out,
        "is_estimate": guessed,
        "sleep":       _extract_sleep(fb.get("sleep", {})),
        "hrv":         _extract_hrv(fb.get("hrv", {})),
        "meals":       meals,
        "workouts":    workouts,
        "fitbit":      fb,
    }

def _get_daily_summary(date_str: str, *, force_fresh: bool = False) -> Dict[str, Any]:
    if not force_fresh and date_str in CACHE:
        return CACHE[date_str]
    summary = _build_daily_summary(date_str)
    if not summary["is_estimate"]:
        CACHE[date_str] = summary
    return summary

# ────────────────  Summary endpoints  ────────────────
@app.get("/data/daily-summary")
def daily_summary(date: Optional[str] = None, fresh: bool = False):
    target = date or _today_se().isoformat()
    return _get_daily_summary(target, force_fresh=fresh)

@app.get("/daily-summary")  # alias / deprecated
def daily_summary_alias(target_date: Optional[str] = None, fresh: bool = False):
    return daily_summary(date=target_date, fresh=fresh)

# ────────────────  Små Fitbit-proxys  ────────────────
@app.get("/data/steps")
def steps(date: str):
    return _fitbit_get("activities/steps", date, date)

@app.get("/data/sleep")
def sleep(date: str):
    return _fitbit_get("sleep", date, date)

@app.get("/data/heart")
def heart(date: str):
    return _fitbit_get("activities/heart", date, date)

@app.get("/data/calories")
def calories(date: str):
    return _fitbit_get("activities/calories", date, date)

# ────────────────  Extended FULL (Fitbit + Firestore)  ────────────────
@app.get("/data/extended/full")
def extended_full(days: int = 1, fresh: bool = False):
    if days < 1:
        raise HTTPException(status_code=400, detail="days måste vara ≥ 1")
    today      = _today_se()
    start_date = today - timedelta(days=days - 1)
    dates      = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "from": dates[0], "to": dates[-1],
        "days": {d: _get_daily_summary(d, force_fresh=fresh) for d in dates},
    }
