"""
🏋️‍♂️ FitGPT – main.py (rev 2025‑07‑03)
────────────────────────────────────────
• En enda källa för HELA dagens sammanfattning ("daily summary")
• Behåller alla tidigare endpoints (ingen bryter!)
• /data/daily-summary är nu den primära vägen GPT använder
• Tydlig tidszons‑hantering (Europe/Stockholm)
• Smart TTL‑cache – bara EXAKT kcal_out cachas
• Förenklade hjälpfunktioner & strikt typning
• Kompaktare imports (endast det som verkligen behövs)
"""

# ────────────────────────────────────────────────────────────
# 🚀 Imports
# ────────────────────────────────────────────────────────────
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field
from typing import Optional, List, Tuple, Set, Dict, Any
import requests, json, os, base64, time, re
from datetime import datetime, timedelta, date as dt_date
from zoneinfo import ZoneInfo
from cachetools import TTLCache
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore  # type: ignore – Editor‑hint: needs GC SDK

# ────────────────────────────────────────────────────────────
# 🌱 Init & miljövariabler
# ────────────────────────────────────────────────────────────
load_dotenv()
SE_TZ = ZoneInfo("Europe/Stockholm")

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"

# ────────────────────────────────────────────────────────────
# ⚙️  FastAPI‑instans
# ────────────────────────────────────────────────────────────
app = FastAPI(title="FitGPT API")
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ────────────────────────────────────────────────────────────
# 🔗 Firestore‑klient
# ────────────────────────────────────────────────────────────
cred_info_raw = os.getenv("FIREBASE_CRED_JSON", "{}")
cred_info: Dict[str, Any] = json.loads(cred_info_raw)
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))

# ────────────────────────────────────────────────────────────
# 🎯 Pydantic‑modeller
# ────────────────────────────────────────────────────────────
class MealLog(BaseModel):
    date: str  # YYYY‑MM‑DD
    meal: str
    items: str
    estimated_calories: Optional[int] = None


class WorkoutLog(BaseModel):
    date: str  # YYYY‑MM‑DD
    workout_type: str = Field(..., alias="type", description="Typ av pass, t.ex. Badminton")
    details: str
    start_time: Optional[str] = Field(
        None,
        alias="startTime",
        description="ISO‑tid (YYYY‑MM‑DDTHH:MM:SS). Tomt ⇒ FitGPT gissar.",
    )

    class Config:
        allow_population_by_field_name = True

# ────────────────────────────────────────────────────────────
# 🔒 Valfri API‑nyckel (Bearer <API_KEY>)
# ────────────────────────────────────────────────────────────
API_KEY_REQUIRED = os.getenv("API_KEY")

def verify_auth(request: Request):
    if not API_KEY_REQUIRED:
        return  # Off – inga krav
    token = request.headers.get("authorization")
    if token != f"Bearer {API_KEY_REQUIRED}":
        raise HTTPException(status_code=401, detail="Missing/invalid token")

# ────────────────────────────────────────────────────────────
# 🏠 Mini‑UI (bara för test)
# ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home():
    return (
        "<h1>FitGPT‑API 🚀</h1>"
        "<ul>"
        "<li><a href='/authorize'>Logga in med Fitbit</a></li>"
        "<li><a href='/docs'>Swagger‑dokumentation</a></li>"
        "</ul>"
    )

# ────────────────────────────────────────────────────────────
# 🔑 Fitbit OAuth‑flöde
# ────────────────────────────────────────────────────────────
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

# ────────────────────────────────────────────────────────────
# 🧩 Hjälp: tidszon & idag
# ────────────────────────────────────────────────────────────

def _today_se() -> dt_date:
    """Returnerar dagens datum i svensk tidzon (date‑objekt)."""
    return datetime.now(SE_TZ).date()

# ────────────────────────────────────────────────────────────
# 📁 Användar‑profil (lokal json‑fil)
# ────────────────────────────────────────────────────────────

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
        raise HTTPException(status_code=400, detail="Body måste vara ett JSON‑objekt.")
    _save_profile(profile)
    return {"message": "✅ Sparat!", "profile": profile}

# ────────────────────────────────────────────────────────────
# 🔥 Firestore – MÅLTIDER
# ────────────────────────────────────────────────────────────
MEAL_COL = db.collection("meals")

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

# ────────────────────────────────────────────────────────────
# 🔥 Firestore – TRÄNINGSPASS
# ────────────────────────────────────────────────────────────
WORKOUT_COL = db.collection("workouts")

# ---------- Heuristik‑helpers ----------

def _extract_duration_min(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)\s*(?:min|\bmins?\b|\bm\b)", text.lower())
    return int(m.group(1)) if m else None


def _guess_auto_match(m_entry: Dict[str, Any], auto_logs: List[Dict[str, Any]], used_idx: Set[int]) -> Tuple[Optional[int], float]:
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

        dur_a = a.get("duration", 0) / 60000  # ms → min
        if dur_m:
            diff = abs(dur_a - dur_m) / max(dur_a, dur_m, 1)
            score += 0.4 if diff <= 0.05 else 0.2 if diff <= 0.15 else 0.0

        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx, best_score

# ---------- Fitbit API helpers ----------

def _fitbit_auth_header() -> Dict[str, str]:
    auth_header = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {auth_header}", "Content-Type": "application/x-www-form-urlencoded"}


def _token_file_exists() -> bool:
    return os.path.exists(TOKEN_FILE)


def _read_token() -> Dict[str, Any]:
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _write_token(data: Dict[str, Any]):
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _refresh_token_if_needed() -> Optional[Dict[str, Any]]:
    if not _token_file_exists():
        return None
    token_data = _read_token()

    exp_secs = token_data.get("expires_in", 28800)  # 8 h default
    saved_at = token_data.get("_saved_at", 0)
    if time.time() < saved_at + exp_secs - 60:
        return token_data  # giltig

    resp = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers=_fitbit_auth_header(),
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        },
    )
    if resp.status_code == 200:
        new_token = resp.json()
        new_token["_saved_at"] = time.time()
        _write_token(new_token)
        return new_token
    return None


def _fitbit_get(resource_path: str, start_date: str, end_date: str) -> Dict[str, Any]:
    token = _refresh_token_if_needed()
    if not token:
        return {"error": "Ingen giltig token."}

    headers = {"Authorization": f"Bearer {token['access_token']}")}
    url = f"https://api.fitbit.com/1/user/-/{resource_path}/date/{start_date}/{end_date}.json"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 429:  # rate‑limit
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
    headers = {"Authorization": f"Bearer {token['access_token']}")}
    url = (
        "https://api.fitbit.com/1/user/-/activities/list.json"
        f"?beforeDate={date_str}T23:59:59&sort=desc&limit=50&offset=0"
    )
    try:
        raw = requests.get(url, headers=headers).json().get("activities", [])
        return [a for a in raw if a.get("originalStartTime", "").startswith(date_str)]
    except Exception:
        return []

# ---------- Workout CRUD ----------

def _infer_start_time(entry: WorkoutLog) -> Tuple[Optional[str], bool]:
    auto_logs = _fitbit_activity_logs(entry.date)
    idx, conf = _guess_auto_match(entry.dict(by_alias=True, exclude_none=True), auto_logs, set())
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

# ────────────────────────────────────────────────────────────
# 🧬 Slå ihop manuella + Fitbit‑pass (samma logik men uppstädad)
# ────────────────────────────────────────────────────────────

def _combine_workouts(date_str: str) -> List[Dict[str, Any]]:
    manual_raw = get_workouts(date_str)
    manual = [{**w, "source": "manual"} for w in manual_raw]

    auto_raw = _fitbit_activity_logs(date_str)
    auto = [{**a, "source": "fitbit"} for a in auto_raw]

    merged: List[Dict[str, Any]] = []
    used_auto: Set[int] = set()

    # 1. Match manuella som har start_time (±30 min)
    for m in manual:
        st = m.get("start_time") or m.get("startTime")
        if not st:
            continue
        try:
            m_ts = datetime.fromisoformat(st)
        except Exception:
            merged.append(m)
            continue

        matched = False
        for idx, a in enumerate(auto):
            if idx in used_auto:
                continue
            try:
                a_ts = datetime.fromisoformat(a["originalStartTime"][:-6])
            except Exception:
                continue
            if abs((a_ts - m_ts).total_seconds()) < 1800:  # 30 min
                used_auto.add(idx)
                merged.append({**a, **m, "source": "merged"})
                matched = True
                break
        if not matched:
            merged.append(m)

    # 2. Manuella UTAN start_time → heuristik
    for m in [x for x in manual if not (x.get("start_time") or x.get("startTime"))]:
        idx, conf = _guess_auto_match(m, auto, used_auto)
        if idx is not None and conf >= 0.8:
            used_auto.add(idx)
            m["start_time"] = auto[idx]["originalStartTime"][:-6]
            merged.append({**auto[idx], **m, "source": "merged"})
        else:
            merged.append({**m, "needs_confirmation": True})

    # 3. Resterande Fitbit‑pass som inte matchats
    merged.extend([a for i, a in enumerate(auto) if i not in used_auto])
    return merged

# ────────────────────────────────────────────────────────────
# 📦 Daily summary – exakt kcal_out + smart cache
# ────────────────────────────────────────────────────────────
DAILY_CACHE = TTLCache(maxsize=64, ttl=60)  # 60 s


def _sum_calories(meal_docs: List[Dict[str, Any]]) -> int:
    return sum(m.get("estimated_calories", 0) or 0 for m in meal_docs)


def _extract_sleep_metrics(blob: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        nights = blob.get("data", {}).get("sleep", [])
        if not nights:
            return None
        total_ms = sum(n["duration"] for n in nights)
        minutes = total_ms // 60000
        eff = round(sum(n.get("efficiency", 0) for n in nights) / len(nights))
        return {"minutes": minutes, "efficiency": eff}
    except Exception:
        return None


def _extract_hrv(blob: Dict[str, Any]) -> Optional[int]:
    try:
        series = blob.get("data", {}).get("hrv", [])
        return int(series[0]["value"]["rmssd"]) if series else None
    except Exception:
        return None


def _extract_daily_kcal_out(blob: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    try:
        acts = blob.get("activities-calories") or blob["data"]["activities-calories"]
        return int(acts[0]["value"]), False
    except Exception:
        return None, True  # inget exakt värde


# ---------- kärnfunktion ----------

def _build_daily_summary(date_str: str) -> Dict[str, Any]:
    meals    = get_meals(date_str)
    workouts = _combine_workouts(date_str)

    # Fitbit‑data i bulk (ett nätverks‑anrop per resurstyp)
    fb        = get_extended(target_date=date_str)
    kcal_out, guessed = _extract_daily_kcal_out(fb.get("calories", {}))
    if guessed:
        kcal_out = None

    summary = {
        "date":        date_str,
        "kcal_in":     _sum_calories(meals),
        "kcal_out":    kcal_out,
        "is_estimate": guessed,
        "sleep":       _extract_sleep_metrics(fb.get("sleep", {})),
        "hrv":         _extract_hrv(fb.get("hrv", {})),
        "meals":       meals,
        "workouts":    workouts,
        "fitbit":      fb,
    }
    return summary


def _get_daily_summary(date_str: str, *, force_fresh: bool = False) -> Dict[str, Any]:
    if not force_fresh and date_str in DAILY_CACHE:
        return DAILY_CACHE[date_str]

    summary = _build_daily_summary(date_str)
    if not summary["is_estimate"]:  # endast exakta kcal_out cachas
        DAILY_CACHE[date_str] = summary
    return summary

# ────────────────────────────────────────────────────────────
# 🔄  Endpoints – EN väg in för GPT (/data/daily-summary)
# ────────────────────────────────────────────────────────────
@app.get("/data/daily-summary")
def daily_summary_endpoint(date: Optional[str] = None, fresh: bool = False):
    """Primary endpoint för GPT. Hämtar HELA dagens data i ett JSON‑objekt.

    • date utelämnad ⇒ dagens datum (svensk tid)
    • fresh=true      ⇒ kringgår cachen
    """
    target = date or _today_se().isoformat()
    return _get_daily_summary(target, force_fresh=fresh)

# ▸ Bakåtkompatibel /daily-summary (alias)
@app.get("/daily-summary")
def daily_summary_alias(target_date: Optional[str] = None, fresh: bool = False):
    return daily_summary_endpoint(date=target_date, fresh=fresh)

# ────────────────────────────────────────────────────────────
# 🔄  Små Fitbit‑proxy‑endpoints  (oförändrade)
# ────────────────────────────────────────────────────────────
@app.get("/data/steps")
def get_steps(date: str):
    return _fitbit_get("activities/steps", date, date)


@app.get("/data/sleep")
def get_sleep(date: str):
    return _fitbit_get("sleep", date, date)


@app.get("/data/heart")
def get_heart(date: str):
    return _fitbit_get("activities/heart", date, date)


@app.get("/data/calories")
def get_calories(date: str):
    return _fitbit_get("activities/calories", date, date)

# ────────────────────────────────────────────────────────────
# 📈  Extended Fitbit‑data (bulk) & FULL (Fitbit + Firestore)
# ────────────────────────────────────────────────────────────
@app.get("/data/extended")
def get_extended(days: int = 1, target_date: Optional[str] = None):
    if target_date:
        start = end = target_date
    else:
        today = _today_se()
        start = (today - timedelta(days=days - 1)).isoformat()
        end = today.isoformat()
    return {
        "from": start,
        "to": end,
        "steps":    _fitbit_get("activities/steps", start, end),
        "calories": _fitbit_get("activities/calories", start, end),
        "sleep":    _fitbit_get("sleep", start, end),
        "heart":    _fitbit_get("activities/heart", start, end),
        "weight":   _fitbit_get("body/log/weight", start, end),
        "hrv":      _fitbit_get("hrv", start, end),
    }


@app.get("/data/extended/full")
def get_extended_full(days: int = 1, fresh: bool = False):
    if days < 1:
        raise HTTPException(status_code=400, detail="days måste vara ≥ 1")

    today      = _today_se()
    start_date = today - timedelta(days=days - 1)
    dates      = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    out: Dict[str, Any] = {"from": dates[0], "to": dates[-1], "days": {}}
    for d in dates:
        out["days"][d] = _get_daily_summary(d, force_fresh=fresh)
    return out

# ────────────────────────────────────────────────────────────
# Finito! – Nu har GPT EN tydlig väg in (data/daily-summary)
# ────────────────────────────────────────────────────────────
