"""
🏋️‍♂️ FitGPT – main.py (rev 2025-07-05 UX-full)
──────────────────────────────────────────────
• Alias-endpoints:  /sammanfatta, /sammanfatta/{idag|igår|YYYY-MM-DD}
• Param days_back   (ex:  /sammanfatta?days_back=2)
• Toast-svar & cache-invalidering när du loggar
• Automatisk start_time = nu (om ingen tid finns) + heuristisk gissning
• Konsekventa svenska vägar /logga/måltid  &  /logga/pass
• Alla gamla /data/*-proxy-endpoints kvar för bakåt­kompatibilitet
• Svensk tidszon (Europe/Stockholm) + hälsokontroll-endpoint
"""

from __future__ import annotations

# ─────────────── Standard & 3P ───────────────
import os, json, re, time, base64, requests
from datetime import datetime, timedelta, date as dt_date
from typing import Optional, List, Tuple, Dict, Any, Set

from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from zoneinfo import ZoneInfo
from cachetools import TTLCache
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore  # pip install google-cloud-firestore

# ─────────────── Init ───────────────
load_dotenv()
SE_TZ = ZoneInfo("Europe/Stockholm")

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"
API_KEY_REQUIRED     = os.getenv("API_KEY")

# ─────────────── FastAPI ───────────────
app = FastAPI(title="FitGPT-API")
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────── Firestore ───────────────
cred_info: Dict[str, Any] = json.loads(os.getenv("FIREBASE_CRED_JSON", "{}"))
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))
MEAL_COL    = db.collection("meals")
WORKOUT_COL = db.collection("workouts")

# ─────────────── Alias-/datum-hjälp ───────────────
ALIAS = {"idag": 0, "igår": 1, "förrgår": 2}


def _today_se() -> dt_date:
    return datetime.now(SE_TZ).date()


def _resolve_date(value: Optional[str] = None, *, days_back: Optional[int] = None) -> str:
    """Returnera ISO-datum med stöd för alias & days_back."""
    if value:
        value = value.lower()
        if value in ALIAS:
            return (_today_se() - timedelta(days=ALIAS[value])).isoformat()
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            raise HTTPException(400, f"Ogiltigt datum/alias: {value}")
    if days_back is not None:
        return (_today_se() - timedelta(days=days_back)).isoformat()
    return _today_se().isoformat()

# ─────────────── Cache ───────────────
CACHE = TTLCache(maxsize=128, ttl=60)
_cache_get = CACHE.get
_cache_set = CACHE.__setitem__
_cache_invalidate = CACHE.pop

# ───────────────  Pydantic modeller  ───────────────
class MealLog(BaseModel):
    date: str
    meal: str
    items: str
    estimated_calories: Optional[int] = None

    _iso = validator("date", allow_reuse=True)(
        lambda v: datetime.fromisoformat(v).date().isoformat()  # type: ignore
    )


class WorkoutLog(BaseModel):
    date: str
    type: str
    details: str
    start_time: Optional[str] = Field(
        None,
        alias="startTime",
        description="ISO-tid. Tomt ⇒ FitGPT gissar eller sätter nu.",
    )

    _iso = validator("date", allow_reuse=True)(
        lambda v: datetime.fromisoformat(v).date().isoformat()  # type: ignore
    )

    class Config:
        allow_population_by_field_name = True

# ─────────────── Auth helper ───────────────
def verify_auth(request: Request):
    if not API_KEY_REQUIRED:
        return
    token = request.headers.get("authorization")
    if token != f"Bearer {API_KEY_REQUIRED}":
        raise HTTPException(401, "Missing or invalid token")

# ─────────────── Mini-UI ───────────────
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT-API 🚀</h1>"
        "<p>Snabbkommandon:</p>"
        "<ul>"
        "<li>/sammanfatta – dagens summering</li>"
        "<li>/sammanfatta/igår – gårdagen</li>"
        "<li>/logga/måltid  •  /logga/pass</li>"
        "</ul>"
        "<p><a href='/docs'>Swagger</a></p>"
    )

# ─────────────── Fitbit OAuth ───────────────
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
    auth_header = base64.b64encode(
        f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()
    ).decode()

    resp = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "client_id": FITBIT_CLIENT_ID,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
    )
    data = resp.json()
    if "access_token" in data:
        data["_saved_at"] = time.time()
        json.dump(data, open(TOKEN_FILE, "w", encoding="utf-8"))
        return {"message": "✅ Token sparad"}
    raise HTTPException(400, data)

# ─────────────── Profil-endpoints ───────────────
def _load_profile() -> Dict[str, Any]:
    if not os.path.exists(PROFILE_FILE):
        return {}
    try:
        return json.load(open(PROFILE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_profile(profile: Dict[str, Any]):
    json.dump(profile, open(PROFILE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


@app.get("/user_profile")
def get_profile():
    return _load_profile()


@app.post("/user_profile")
def set_profile(profile: Dict[str, Any]):
    if not isinstance(profile, dict):
        raise HTTPException(400, "Body måste vara ett JSON-objekt.")
    _save_profile(profile)
    return {"message": "✅ Sparat!", "profile": profile}

# ─────────────── Fitbit helpers ───────────────
def _fitbit_auth_header() -> Dict[str, str]:
    b64 = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {b64}", "Content-Type": "application/x-www-form-urlencoded"}


def _read_token():
    return json.load(open(TOKEN_FILE))


def _write_token(t):
    json.dump(t, open(TOKEN_FILE, "w"))


def _refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None
    t = _read_token()
    if time.time() < t.get("_saved_at", 0) + t.get("expires_in", 28800) - 60:
        return t
    r = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers=_fitbit_auth_header(),
        data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"]},
    )
    if r.status_code == 200:
        new = r.json(); new["_saved_at"] = time.time(); _write_token(new); return new
    return None


def _fitbit_get(path: str, start: str, end: str):
    tok = _refresh_token_if_needed()
    if not tok:
        return {"error": "Ingen giltig token."}
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    url = f"https://api.fitbit.com/1/user/-/{path}/date/{start}/{end}.json"
    r = requests.get(url, headers=h)
    if r.status_code == 429:
        time.sleep(int(r.headers.get("Retry-After", 5)))
        r = requests.get(url, headers=h)
    try:
        r.raise_for_status(); return {"data": r.json()}
    except Exception as e:
        return {"error": str(e)}

def _fitbit_activity_logs(date_str: str):
    tok = _refresh_token_if_needed()
    if not tok: return []
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    url = (
        "https://api.fitbit.com/1/user/-/activities/list.json"
        f"?beforeDate={date_str}T23:59:59&sort=desc&limit=50&offset=0"
    )
    try:
        acts = requests.get(url, headers=h).json().get("activities", [])
        return [a for a in acts if a.get("originalStartTime", "").startswith(date_str)]
    except Exception:
        return []

# ─────────────── Workout-merge-helpers ───────────────
def _extract_duration_min(text: str):
    m = re.search(r"(\\d+)\\s*(?:min|\\bmins?\\b|\\bm\\b)", text.lower()) if text else None
    return int(m.group(1)) if m else None


def _guess_auto_match(m_entry: Dict[str, Any], auto_logs, used):
    dur_m = _extract_duration_min(m_entry.get("details", "")) or None
    wt_m  = m_entry.get("type", "").lower()
    best_idx, best_score = None, 0.0
    for idx, a in enumerate(auto_logs):
        if idx in used: continue
        name = a.get("activityName", "").lower(); score = 0.0
        if wt_m and wt_m in name: score += 0.6
        elif name and name in wt_m: score += 0.4
        dur_a = a.get("duration", 0) / 60000
        if dur_m:
            diff = abs(dur_a - dur_m) / max(dur_a, dur_m, 1)
            score += 0.4 if diff <= 0.05 else 0.2 if diff <= 0.15 else 0
        if score > best_score: best_idx, best_score = idx, score
    return best_idx, best_score


def _infer_start_time(entry: WorkoutLog):
    auto = _fitbit_activity_logs(entry.date)
    idx, conf = _guess_auto_match(entry.dict(by_alias=True, exclude_none=True), auto, set())
    if idx is not None and conf >= 0.6:
        return auto[idx]["originalStartTime"][:-6]
    return None

# ─────────────── CRUD-endpoints: Måltid ───────────────
@app.post("/logga/måltid", dependencies=[Depends(verify_auth)])
@app.post("/log/meal",    dependencies=[Depends(verify_auth)])  # bakåtkomp.
def post_meal(entry: MealLog = Body(...)):
    doc_id = f"{entry.date}-{entry.meal.lower()}"
    MEAL_COL.document(doc_id).set(entry.dict(exclude_none=True))
    _cache_invalidate(entry.date)
    daily = _get_daily_summary(entry.date, force_fresh=True)
    return {
        "toast": f"✅ Måltid '{entry.meal}' loggad.",
        "daily": daily,
        "id": doc_id,
    }


@app.get("/logga/måltid")
@app.get("/log/meal")
def get_meals(date: str):
    docs = MEAL_COL.where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

# ─────────────── CRUD-endpoints: Pass ───────────────
@app.post("/logga/pass", dependencies=[Depends(verify_auth)])
@app.post("/log/workout", dependencies=[Depends(verify_auth)])  # bakåtkomp.
def post_workout(entry: WorkoutLog = Body(...)):
    if not entry.start_time:
        entry.start_time = _infer_start_time(entry) or datetime.now(SE_TZ).isoformat()
    doc_id = WORKOUT_COL.add(entry.dict(by_alias=True, exclude_none=True))[1].id
    _cache_invalidate(entry.date)
    daily = _get_daily_summary(entry.date, force_fresh=True)
    return {
        "toast": f"✅ Pass '{entry.type}' loggat.",
        "daily": daily,
        "id": doc_id,
    }


@app.get("/logga/pass")
@app.get("/log/workout")
def get_workouts(date: str):
    docs = WORKOUT_COL.where("date", "==", date).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

# ─────────────── Merge-pass (manual+auto) ───────────────
def _combine_workouts(date_str: str):
    manual = [{**w, "source": "manual"} for w in get_workouts(date_str)]
    auto   = [{**a, "source": "fitbit"} for a in _fitbit_activity_logs(date_str)]
    merged, used = [], set()

    # 1) match manuella med start_time
    for m in manual:
        st = m.get("start_time") or m.get("startTime")
        if not st: continue
        try: m_ts = datetime.fromisoformat(st)
        except Exception: merged.append(m); continue
        matched = False
        for idx, a in enumerate(auto):
            if idx in used: continue
            try: a_ts = datetime.fromisoformat(a["originalStartTime"][:-6])
            except Exception: continue
            if abs((a_ts - m_ts).total_seconds()) < 1800:
                used.add(idx); merged.append({**a, **m, "source": "merged"}); matched = True; break
        if not matched: merged.append(m)

    # 2) manuella utan tid
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

# ─────────────── Daily summary helpers ───────────────
def _sum_cals(meals):
    return sum(m.get("estimated_calories", 0) or 0 for m in meals)


def _extract_sleep(blob):
    nights = blob.get("data", {}).get("sleep", [])
    if not nights: return None
    total = sum(n.get("duration", 0) for n in nights)
    return {
        "minutes": total // 60000,
        "efficiency": round(sum(n.get("efficiency", 0) for n in nights) / len(nights)),
    }


def _extract_hrv(blob):
    series = blob.get("data", {}).get("hrv", [])
    return int(series[0]["value"].get("rmssd")) if series else None


def _extract_kcal_out(blob):
    try:
        acts = blob.get("activities-calories") or blob["data"]["activities-calories"]
        return int(acts[0]["value"]), False
    except Exception:
        return None, True

# ─────────────── Extended Fitbit wrapper ───────────────
def _get_extended(date_str: str):
    return {
        "steps":    _fitbit_get("activities/steps", date_str, date_str),
        "calories": _fitbit_get("activities/calories", date_str, date_str),
        "sleep":    _fitbit_get("sleep", date_str, date_str),
        "heart":    _fitbit_get("activities/heart", date_str, date_str),
        "weight":   _fitbit_get("body/log/weight", date_str, date_str),
        "hrv":      _fitbit_get("hrv", date_str, date_str),
    }

# ─────────────── Daily summary core ───────────────
def _build_daily_summary(date_str: str):
    meals    = get_meals(date_str)
    workouts = _combine_workouts(date_str)
    fb       = _get_extended(date_str)

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


def _get_daily_summary(date_str: str, *, force_fresh=False):
    if not force_fresh:
        cached = _cache_get(date_str)
        if cached: return cached
    s = _build_daily_summary(date_str)
    if not s["is_estimate"]:
        _cache_set(date_str, s)
    return s

# ─────────────── Summary endpoints ───────────────
@app.get("/sammanfatta")
@app.get("/sammanfatta/{datum}")
@app.get("/data/daily-summary")  # gamla vägen
def sammanfatta(datum: Optional[str] = None,
                days_back: Optional[int] = None,
                fresh: bool = False):
    target = _resolve_date(datum, days_back=days_back)
    return _get_daily_summary(target, force_fresh=fresh)

# ─────────────── Fitbit-proxy endpoints ───────────────
@app.get("/data/steps");    def steps(date: str):    return _fitbit_get("activities/steps", date, date)
@app.get("/data/sleep");    def sleep(date: str):    return _fitbit_get("sleep", date, date)
@app.get("/data/heart");    def heart(date: str):    return _fitbit_get("activities/heart", date, date)
@app.get("/data/calories"); def calories(date: str): return _fitbit_get("activities/calories", date, date)

# ─────────────── Extended FULL (Fitbit + Firestore) ───────────────
@app.get("/data/extended/full")
def extended_full(days: int = 1, fresh: bool = False):
    if days < 1:
        raise HTTPException(400, "days måste vara ≥ 1")
    today      = _today_se()
    start_date = today - timedelta(days=days - 1)
    dates      = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "from": dates[0], "to": dates[-1],
        "days": {d: _get_daily_summary(d, force_fresh=fresh) for d in dates},
    }

# ─────────────── Healthcheck ───────────────
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(SE_TZ).isoformat()}
