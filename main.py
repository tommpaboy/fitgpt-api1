# 🏋️‍♂️ FitGPT – main.py  (rev 2025-07-06 stable + snapshot-patch 2025-07-05)
# ────────────────────────────────────────────────────────────────────────────
# • Alias /sammanfatta  (idag|igår|YYYY-MM-DD)  + days_back
# • Svenska CRUD-vägar  /logga/måltid   /logga/pass
# • WorkoutLog accepterar “type” OCH “workout_type”
# • Heuristisk start_time, cache-invalidering, toast-svar
# • Legacy-proxyer och /daily-summary finns kvar
# • Bugfixar:
#   – _combine_workouts anropar helper (ej route)
#   – merged/used typannotering delad
#   – säker cache.pop
#   – HRV NoneType
#   – konsekvent indrag
# • NYTT: dagliga snapshots + ETag-cache (/v1/summaries/daily)
# • UPPDATERAT (2025-08-18): MealLog.items = LISTA AV STRÄNGAR (List[str]), 201-svar på /log/meal,
#   fallback om meal saknas, samt /_echo för diagnostik – alla med auth.

from __future__ import annotations

# ─────────  Standard & 3P  ─────────
import os, json, re, time, base64, requests
from datetime import datetime, timedelta, timezone, date as dt_date
from typing import Optional, List, Dict, Any, Set

from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator, root_validator, ConfigDict
from zoneinfo import ZoneInfo
from cachetools import TTLCache
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.cloud import firestore

# ─────────  Init  ─────────
load_dotenv()
SE_TZ = ZoneInfo("Europe/Stockholm")

FITBIT_CLIENT_ID     = os.getenv("FITBIT_CLIENT_ID")
FITBIT_CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://fitgpt-2364.onrender.com/callback")
TOKEN_FILE           = "fitbit_token.json"
PROFILE_FILE         = "user_profile.json"
API_KEY_REQUIRED     = os.getenv("API_KEY")

# ─────────  FastAPI  ─────────
app = FastAPI(title="FitGPT-API")
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="well-known")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────  Firestore  ─────────
cred_info: Dict[str, Any] = json.loads(os.getenv("FIREBASE_CRED_JSON", "{}"))
firebase_creds = service_account.Credentials.from_service_account_info(cred_info)
db = firestore.Client(credentials=firebase_creds, project=cred_info.get("project_id"))
MEAL_COL        = db.collection("meals")
WORKOUT_COL     = db.collection("workouts")
SNAPSHOT_COL    = db.collection("daily_snapshots")          # 🆕 snapshot-samling

# ─────────  Datum-helpers  ─────────
ALIAS = {"idag": 0, "igår": 1, "förrgår": 2}


def _today_se() -> dt_date:
    return datetime.now(SE_TZ).date()


def _resolve_date(val: Optional[str] = None, *, days_back: Optional[int] = None) -> str:
    if val:
        v = val.lower()
        if v in ALIAS:
            return (_today_se() - timedelta(days=ALIAS[v])).isoformat()
        try:
            return datetime.fromisoformat(v).date().isoformat()
        except ValueError:
            raise HTTPException(400, f"Ogiltigt datum/alias: {val}")
    if days_back is not None:
        return (_today_se() - timedelta(days=days_back)).isoformat()
    return _today_se().isoformat()

# ─────────  Cache  ─────────
CACHE = TTLCache(maxsize=128, ttl=60)
_cache_get        = CACHE.get
_cache_set        = CACHE.__setitem__
_cache_invalidate = lambda k: CACHE.pop(k, None)        # safe pop

# ─────────  Pydantic-modeller  ─────────
class MealLog(BaseModel):
    date: str
    meal: Optional[str] = None
    items: List[str]  # ⬅️ ändrat: lista av strängar
    estimated_calories: Optional[int] = None

    _iso = validator("date", allow_reuse=True)(
        lambda v: datetime.fromisoformat(v).date().isoformat()  # type: ignore
    )


class WorkoutLog(BaseModel):
    date: str
    workout_type: str = Field(..., alias="type")
    details: str
    start_time: Optional[str] = Field(None, alias="startTime")

    _iso = validator("date", allow_reuse=True)(
        lambda v: datetime.fromisoformat(v).date().isoformat()  # type: ignore
    )

    @root_validator(pre=True)
    def _coerce_or_error(cls, values):
        has_new = "type" in values
        has_old = "workout_type" in values

        if has_new and has_old:
            raise ValueError("Skicka antingen 'type' ELLER 'workout_type' – inte båda.")

        if has_old and not has_new:
            values["type"] = values.pop("workout_type")

        return values

    class Config:
        allow_population_by_field_name = True
        allow_population_by_alias      = True
        extra = "forbid"

# ─────────  Auth helper  ─────────
def verify_auth(request: Request):
    if API_KEY_REQUIRED and request.headers.get("authorization") != f"Bearer {API_KEY_REQUIRED}":
        raise HTTPException(401, "Missing or invalid token")

# ─────────  Mini-UI  ─────────
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <h1>FitGPT-API 🚀</h1>
    <ul>
      <li><a href='/authorize'>Logga in med Fitbit</a></li>
      <li><a href='/docs'>Swagger</a></li>
      <li><a href='/sammanfatta'>/sammanfatta</a></li>
    </ul>
    """

# ─────────  Fitbit OAuth  ─────────
@app.get("/authorize")
def authorize():
    scope = "activity nutrition sleep heartrate weight location profile"
    url = (
        "https://www.fitbit.com/oauth2/authorize?response_type=code"
        f"&client_id={FITBIT_CLIENT_ID}&redirect_uri={REDIRECT_URI}"
        f"&scope={scope.replace(' ', '%20')}"
    )
    return RedirectResponse(url)


@app.get("/callback")
def callback(code: str):
    b64 = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://api.fitbit.com/oauth2/token",
        headers={"Authorization": f"Basic {b64}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"client_id": FITBIT_CLIENT_ID,
              "grant_type": "authorization_code",
              "redirect_uri": REDIRECT_URI,
              "code": code},
    )
    data = r.json()
    if "access_token" in data:
        data["_saved_at"] = time.time()
        json.dump(data, open(TOKEN_FILE, "w"))
        return {"message": "✅ Token sparad"}
    raise HTTPException(400, data)
    
# ─────────  Tid-endpoint  ─────────
@app.get("/time")
def current_time():
    """Returnerar serverns aktuella tid i Europe/Stockholm (tz-aware ISO-8601)."""
    return {
        "time": datetime.now(SE_TZ).isoformat(timespec="seconds")
    }

# ─────────  Profil-endpoints  ─────────
def _load_profile() -> Dict[str, Any]:
    return json.load(open(PROFILE_FILE)) if os.path.exists(PROFILE_FILE) else {}

def _save_profile(p: Dict[str, Any]):
    json.dump(p, open(PROFILE_FILE, "w"), ensure_ascii=False, indent=2)

@app.get("/user_profile")
def get_profile(): return _load_profile()

@app.post("/user_profile")
def set_profile(p: Dict[str, Any]):
    if not isinstance(p, dict):
        raise HTTPException(400, "Body måste vara ett JSON-objekt.")
    _save_profile(p)
    return {"message": "✅ Sparat!", "profile": p}

# ─────────  Fitbit helpers  ─────────
def _fitbit_auth_header():
    b64 = base64.b64encode(f"{FITBIT_CLIENT_ID}:{FITBIT_CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {b64}", "Content-Type": "application/x-www-form-urlencoded"}

def _read_token():  return json.load(open(TOKEN_FILE))
def _write_token(t): json.dump(t, open(TOKEN_FILE, "w"))

def _refresh_token_if_needed():
    if not os.path.exists(TOKEN_FILE):
        return None
    t = _read_token()
    if time.time() < t["_saved_at"] + t.get("expires_in", 28800) - 60:
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
        r.raise_for_status()
        return {"data": r.json()}
    except Exception as e:
        return {"error": str(e)}


def _fitbit_activity_logs(date_str: str):
    tok = _refresh_token_if_needed()
    if not tok:
        return []
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    url = (
        "https://api.fitbit.com/1/user/-/activities/list.json"
        f"?beforeDate={date_str}T23:59:59&sort=desc&limit=50&offset=0"
    )
    try:
        raw = requests.get(url, headers=h).json().get("activities", [])
        return [a for a in raw if a.get("originalStartTime", "").startswith(date_str)]
    except Exception:
        return []

# ─────────  Workout-helpers  ─────────
def _extract_duration_min(t: str):
    m = re.search(r"(\d+)\s*(?:min|\bmins?\b|\bm\b)", t.lower()) if t else None
    return int(m.group(1)) if m else None


def _guess_auto_match(m: Dict[str, Any], auto_logs, used: Set[int]):
    dur_m = _extract_duration_min(m.get("details", "")) or None
    wt_m = m.get("type", "").lower()
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
        if dur_m is not None:
            diff = abs(dur_a - dur_m) / max(dur_a, dur_m, 1)
            score += 0.4 if diff <= 0.05 else 0.2 if diff <= 0.15 else 0.0
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx, best_score


def _infer_start_time(entry: WorkoutLog):
    auto = _fitbit_activity_logs(entry.date)
    idx, conf = _guess_auto_match(entry.dict(by_alias=True, exclude_none=True), auto, set())
    if idx is not None and conf >= 0.6:
        return auto[idx]["originalStartTime"][:-6]
    return None

# ─────────  Firestore-helpers  ─────────
def _fetch_meals(d: str) -> List[Dict[str, Any]]:
    return [{"id": doc.id, **doc.to_dict()} for doc in MEAL_COL.where("date", "==", d).stream()]


def _fetch_manual_workouts(d: str) -> List[Dict[str, Any]]:
    return [{"id": doc.id, **doc.to_dict()} for doc in WORKOUT_COL.where("date", "==", d).stream()]

# ─────────  Snapshot-helper  🆕  ─────────
def _update_daily_snapshot(d: str):
    """Bygger dags-sammanfattning och sparar i snapshot-samlingen."""
    summary = _build_daily_summary(d)
    summary["updated_at"] = firestore.SERVER_TIMESTAMP
    SNAPSHOT_COL.document(d).set(summary)

# ─────────  CRUD Meal  ─────────
@app.post("/logga/måltid", status_code=201, dependencies=[Depends(verify_auth)])
@app.post("/log/meal",     status_code=201, dependencies=[Depends(verify_auth)])  # legacy
def post_meal(entry: MealLog = Body(...)):
    meal_name = (entry.meal or "batch").lower()
    doc_id = f"{entry.date}-{meal_name}"
    MEAL_COL.document(doc_id).set(entry.dict(exclude_none=True))
    _cache_invalidate(entry.date)
    _update_daily_snapshot(entry.date)                          # 🆕 håll snapshot aktuell
    # YAML-kompatibelt svar (201)
    return {"ok": True, "inserted_ids": [doc_id], "warnings": []}


@app.get("/logga/måltid")
@app.get("/log/meal")
def get_meals(date: str):
    return _fetch_meals(date)

# ─────────  CRUD Workout  ─────────
@app.post("/logga/pass", dependencies=[Depends(verify_auth)])
@app.post("/log/workout", dependencies=[Depends(verify_auth)])  # legacy
def post_workout(entry: WorkoutLog = Body(...)):
    if not entry.start_time:
        entry.start_time = _infer_start_time(entry) or datetime.now(SE_TZ).isoformat()
    doc_id = WORKOUT_COL.add(entry.dict(by_alias=True, exclude_none=True))[1].id
    _cache_invalidate(entry.date)
    _update_daily_snapshot(entry.date)                          # 🆕 håll snapshot aktuell
    daily = _get_daily_summary(entry.date, force_fresh=True)
    return {"toast": f"✅ Pass '{entry.workout_type}' loggat.", "daily": daily, "id": doc_id}


@app.get("/logga/pass")
@app.get("/log/workout")
def get_workouts(date: str):
    return _fetch_manual_workouts(date)

# ─────────  Merge-pass  ─────────
def _combine_workouts(d: str):
    """Slår ihop manuella och Fitbit-pass + hanterar tidszon."""
    manual = [{**w, "source": "manual"} for w in _fetch_manual_workouts(d)]
    auto   = [{**a, "source": "fitbit"}  for a in _fitbit_activity_logs(d)]

    merged: List[Dict[str, Any]] = []
    used: Set[int] = set()

    # 1) manuella med start_time
    for m in manual:
        st = m.get("start_time") or m.get("startTime")
        if not st:
            continue
        try:
            m_ts = datetime.fromisoformat(st)
            if m_ts.tzinfo is None:
                m_ts = m_ts.replace(tzinfo=SE_TZ)
        except Exception:
            merged.append(m)
            continue

        matched = False
        for idx, a in enumerate(auto):
            if idx in used:
                continue
            try:
                a_ts = datetime.fromisoformat(a["originalStartTime"][:-6])
                if a_ts.tzinfo is None:
                    a_ts = a_ts.replace(tzinfo=SE_TZ)
            except Exception:
                continue

            if abs((a_ts - m_ts).total_seconds()) < 1800:
                used.add(idx)
                merged.append({**a, **m, "source": "merged"})
                matched = True
                break

        if not matched:
            merged.append(m)

    # 2) manuella utan tid ⇒ heuristisk match
    for m in [x for x in manual if not (x.get("start_time") or x.get("startTime"))]:
        idx, conf = _guess_auto_match(m, auto, used)
        if idx is not None and conf >= 0.8:
            used.add(idx)
            m["start_time"] = auto[idx]["originalStartTime"][:-6]
            merged.append({**auto[idx], **m, "source": "merged"})
        else:
            merged.append({**m, "needs_confirmation": True})

    # 3) lägg till ev. kvarvarande Fitbit-pass
    merged.extend([a for i, a in enumerate(auto) if i not in used])
    return merged


# ─────────  Extract-helpers  ─────────
_sum_cals = lambda meals: sum(m.get("estimated_calories", 0) or 0 for m in meals)


def _extract_sleep(blob):
    nights = blob.get("data", {}).get("sleep", [])
    if not nights:
        return None
    total = sum(n.get("duration", 0) for n in nights)
    return {"minutes": total // 60000,
            "efficiency": round(sum(n.get("efficiency", 0) for n in nights) / len(nights))}


def _extract_hrv(blob):
    try:
        series = blob.get("data", {}).get("hrv", [])
        val = series[0]["value"]["rmssd"] if series else None
        return int(val) if val is not None else None
    except Exception:
        return None


def _extract_kcal_out(blob):
    try:
        acts = blob.get("activities-calories") or blob["data"]["activities-calories"]
        return int(acts[0]["value"]), False
    except Exception:
        return None, True

# ─────────  Fitbit wrapper  ─────────
def _get_extended(d: str):
    return {"steps": _fitbit_get("activities/steps", d, d),
            "calories": _fitbit_get("activities/calories", d, d),
            "sleep": _fitbit_get("sleep", d, d),
            "heart": _fitbit_get("activities/heart", d, d),
            "weight": _fitbit_get("body/log/weight", d, d),
            "hrv": _fitbit_get("hrv", d, d)}

# ─────────  Daily summary  ─────────
def _build_daily_summary(d: str):
    meals = _fetch_meals(d)
    workouts = _combine_workouts(d)
    fb = _get_extended(d)
    kcal_out, guess = _extract_kcal_out(fb.get("calories", {}))
    return {"date": d,
            "kcal_in": _sum_cals(meals),
            "kcal_out": None if guess else kcal_out,
            "is_estimate": guess,
            "sleep": _extract_sleep(fb.get("sleep", {})),
            "hrv": _extract_hrv(fb.get("hrv", {})),
            "meals": meals,
            "workouts": workouts,
            "fitbit": fb}

def _get_daily_summary(d: str, *, force_fresh=False):
    if not force_fresh and (c := _cache_get(d)):
        return c
    s = _build_daily_summary(d)
    if not s["is_estimate"]:
        _cache_set(d, s)
    return s

# ─────────  Summary-endpoints  ─────────
@app.get("/sammanfatta")
@app.get("/sammanfatta/{datum}")
@app.get("/data/daily-summary")  # legacy
def sammanfatta(datum: Optional[str] = None,
                days_back: Optional[int] = None,
                fresh: bool = False):
    target = _resolve_date(datum, days_back=days_back)
    return _get_daily_summary(target, force_fresh=fresh)

@app.get("/daily-summary")  # ännu äldre alias
def daily_summary_alias(date: Optional[str] = None,
                        target_date: Optional[str] = None,
                        fresh: bool = False):
    return sammanfatta(datum=date or target_date, fresh=fresh)

# ─────────  Snapshot endpoint  🆕  ─────────
@app.get("/v1/summaries/daily")
def get_daily_snapshot(date: str, request: Request):
    """Caching-säker daglig snapshot med ETag (löser midnatt-glömskan)."""
    doc = SNAPSHOT_COL.document(date).get()
    if not doc.exists:
        # Skapa snapshot “on demand” första gången
        _update_daily_snapshot(date)
        doc = SNAPSHOT_COL.document(date).get()

    data = doc.to_dict()
    etag = data.get("updated_at")
    if isinstance(etag, datetime):
        etag = etag.isoformat()

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    return JSONResponse(content=data, headers={"ETag": etag})

# ─────────  Fitbit-proxys  ─────────
@app.get("/data/steps")
def proxy_steps(date: str):    return _fitbit_get("activities/steps", date, date)

@app.get("/data/sleep")
def proxy_sleep(date: str):    return _fitbit_get("sleep", date, date)

@app.get("/data/heart")
def proxy_heart(date: str):    return _fitbit_get("activities/heart", date, date)

@app.get("/data/calories")
def proxy_cal(date: str):      return _fitbit_get("activities/calories", date, date)

from traceback import format_exc
from fastapi import status

@app.get("/data/extended/full")
def extended_full(days: int = 1, fresh: bool = False):
    if days < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days måste vara ≥ 1")
    
    try:
        dates = [(_today_se() - timedelta(days=i)).isoformat() for i in reversed(range(days))]
        result = {}

        for d in dates:
            try:
                summary = _get_daily_summary(d, force_fresh=fresh)
                result[d] = summary
            except Exception as day_err:
                print(f"⚠️ Fel vid sammanställning för {d}:")
                print(format_exc())
                result[d] = {"error": f"Kunde inte hämta data för {d}."}

        return {
            "from": dates[0],
            "to": dates[-1],
            "days": result
        }

    except Exception as e:
        print("❌ Allmänt fel i /data/extended/full:")
        print(format_exc())
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Internt fel vid hämtning av dagsdata.")

# ─────────  Healthcheck  ─────────
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(SE_TZ).isoformat()}

# ─────────  Echo-diagnostik (matchar YAML /_echo)  ─────────
@app.post("/_echo", dependencies=[Depends(verify_auth)])
def post_echo(payload: dict = Body(default={})):
    """
    Enkel diagnostik för Actions/Connector.
    Anropa med valfri JSON för att verifiera att POST verkligen träffar servern.
    Kräver Bearer auth likt övriga POST-endpoints.
    """
    try:
        # Logga nycklarna för synlighet i Render-loggar
        keys = list(payload.keys())[:10]
        print(f"/_echo received keys={keys}")
    except Exception:
        pass
    return {"ok": True, "received": payload}
