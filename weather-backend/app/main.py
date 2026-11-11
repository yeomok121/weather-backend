import os
import hmac
import base64
import json
import time
import threading
import unicodedata
import concurrent.futures
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from contextvars import copy_context
from app.weather_diagnostics import (
    RequestLogMiddleware, log_environment_status, run_weather_task,
    log_error, request_id_var,
)
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse
from pymysql import err as db_errors
from app.health import router as health_router, monitor_database
import requests
from app.advice import get_ai_contents
from app.weather import (
    get_current_weather,
    get_hourly_forecast,
    search_location,
    get_air_quality,
    get_weekly_forecast,
    search_mountain,
    get_mountain_weather,
    MOUNTAINS_DB, refresh_mountain_catalog, _mountain_catalog_diagnostics
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import bcrypt
from app.database import get_conn
from app.marine_integrations import check_marine_integrations
from app.fishing import get_fishing_summary

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Track dependency recovery without blocking HTTP startup.
    monitor_task = asyncio.create_task(monitor_database())
    try:
        yield
    finally:
        monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitor_task
# --- Aiven DB 유휴 상태 방지 코드 끝 ---

# 앱 실행 시 lifespan 등록
app = FastAPI(lifespan=lifespan)
app.include_router(health_router)
log_environment_status()


@app.exception_handler(db_errors.OperationalError)
@app.exception_handler(db_errors.InterfaceError)
async def database_unavailable_handler(request, exc):
    log_error("database_unavailable", exc)
    return JSONResponse(status_code=503, headers={"Retry-After": "30"}, content={
        "detail": "회원 정보 서버에 연결하지 못했습니다. 작성한 내용은 유지됩니다. 잠시 후 다시 시도해 주세요.",
        "request_id": request_id_var.get(),
    })


@app.exception_handler(db_errors.IntegrityError)
async def database_conflict_handler(request, exc):
    log_error("database_conflict", exc)
    return JSONResponse(status_code=409, content={"detail": "이미 등록된 항목이거나 변경된 자료입니다. 새로고침한 뒤 다시 확인해 주세요."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)

@app.get("/")
def hello():
    return {"message": "날씨 서버 작동 중!"}

@app.get("/api/weather/summary")
def summary(lat: float, lon: float):
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            f_current = executor.submit(copy_context().run, run_weather_task, "current", get_current_weather, lat, lon)
            f_hourly = executor.submit(copy_context().run, run_weather_task, "hourly", get_hourly_forecast, lat, lon)
            f_aqi = executor.submit(copy_context().run, run_weather_task, "air_quality", get_air_quality, lat, lon)
            f_weekly = executor.submit(copy_context().run, run_weather_task, "weekly", get_weekly_forecast, lat, lon)

            current = f_current.result()
            forecast = f_hourly.result()
            air_quality = f_aqi.result()
            weekly = f_weekly.result()
    except Exception as e:
        log_error("summary_failed", e)
        raise HTTPException(status_code=502, detail=f"날씨 조회 중 오류가 발생했습니다. request_id={request_id_var.get()}") from None
    
    return {
        "current": current, "hourly": forecast, "air_quality": air_quality, "weekly": weekly,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "degraded": not forecast or not weekly or air_quality.get("pm25") is None,
            "missing_fields": [field for field in ("humidity", "wind_speed", "sunrise", "sunset") if current.get(field) is None],
        },
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r

# ---------------------------------------------------------
# ⛰️ 산악 기상 전용 API
# ---------------------------------------------------------
@app.get("/api/mountain/list")
def api_mountain_list():
    refresh_mountain_catalog()
    return [
        {"name": name, **info}
        for name, info in list(MOUNTAINS_DB.items())
    ]

@app.get("/api/mountain/catalog/status")
def api_mountain_catalog_status():
    refresh_mountain_catalog()
    return {"catalog_count": len(MOUNTAINS_DB), "source_check": dict(_mountain_catalog_diagnostics)}

@app.get("/api/mountain/search")
def api_search_mountain(q: str):
    result = search_mountain(q)
    if not result:
        raise HTTPException(status_code=404, detail="해당 산을 찾을 수 없습니다.")
    return result

@app.get("/api/mountain/summary")
def api_mountain_summary(name: str):
    try:
        result = get_mountain_weather(name)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="기상 공급 기관에서 산악 예보를 받지 못했습니다. 잠시 후 다시 확인해 주세요.") from None
    if not result:
        raise HTTPException(status_code=404, detail="산 정보를 불러올 수 없습니다.")
    result["metadata"] = {"generated_at": datetime.now(timezone.utc).isoformat(), "degraded": not result.get("hourly")}
    return result

@app.get("/api/fishing/summary")
def api_fishing_summary(place: str = "추자도 해상", region: str = "", lat: float = 33.96194, lon: float = 126.44527):
    """Public fishing dashboard data from approved KHOA/KMA marine APIs."""
    try:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(status_code=422, detail="위치 좌표 범위를 확인해 주세요.")
        return get_fishing_summary(place=place, region=region, lat=lat, lon=lon)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="해양 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")

class SignUpRequest(BaseModel):
    username: str
    password: str
    name: str

class LoginRequest(BaseModel):
    username: str
    password: str

def _validate_username(username: str) -> str:
    username = username.strip()
    if len(username) < 4 or len(username) > 80:
        raise HTTPException(status_code=400, detail='아이디는 4~80자로 입력해주세요')
    return username

def _validate_password_username(username: str, password: str):
    normalized_id = unicodedata.normalize('NFKC', username).casefold()
    normalized_password = unicodedata.normalize('NFKC', password).casefold()
    if normalized_id and normalized_id in normalized_password:
        raise HTTPException(status_code=400, detail='비밀번호에 아이디를 포함할 수 없습니다')

def _lock_username_changes(cur):
    # Serialize account creation and username changes even when requests race.
    # MySQL releases this connection-scoped lock when the connection closes.
    cur.execute("SELECT GET_LOCK('weather_users_username_v1', 10) AS acquired")
    if cur.fetchone()['acquired'] != 1:
        raise HTTPException(status_code=503, detail='아이디 확인이 지연되고 있습니다. 잠시 후 다시 시도해주세요')

@app.get('/api/auth/username-availability')
def username_availability(username: str):
    username = _validate_username(username)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE username = %s', (username,))
            return {'username': username, 'available': cur.fetchone() is None}
    finally:
        conn.close()

AUTH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7
_favorites_schema_ready = False
_favorites_schema_lock = threading.Lock()
_hiking_schema_ready = False
_hiking_schema_lock = threading.Lock()

def _auth_signing_key() -> bytes:
    secret = os.getenv("AUTH_SECRET_KEY", "")
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="서버 인증 설정이 완료되지 않았습니다")
    return secret.encode("utf-8")

@app.get("/api/auth/status")
def auth_status():
    """Expose whether authentication is configured without revealing its secret."""
    secret = os.getenv("AUTH_SECRET_KEY", "")
    return {
        "configured": len(secret) >= 32,
        "minimum_length": 32,
        "message": "인증을 사용할 수 있습니다" if len(secret) >= 32 else "서버 인증 설정이 완료되지 않았습니다",
    }

def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

def _create_access_token(username: str, version: int = 0) -> str:
    payload = json.dumps({"sub": username, "ver": version, "exp": int(time.time()) + AUTH_TOKEN_TTL_SECONDS}, separators=(",", ":")).encode("utf-8")
    encoded_payload = _encode_token_part(payload)
    signature = hmac.new(_auth_signing_key(), encoded_payload.encode("ascii"), "sha256").digest()
    return f"{encoded_payload}.{_encode_token_part(signature)}"

def _decode_access_token(authorization: str | None, include_payload: bool = False):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    try:
        encoded_payload, encoded_signature = authorization[7:].split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload_bytes = base64.urlsafe_b64decode(encoded_payload + padding)
        signature_padding = "=" * (-len(encoded_signature) % 4)
        signature = base64.urlsafe_b64decode(encoded_signature + signature_padding)
        expected = hmac.new(_auth_signing_key(), encoded_payload.encode("ascii"), "sha256").digest()
        payload = json.loads(payload_bytes)
        if not hmac.compare_digest(signature, expected) or int(payload.get("exp", 0)) <= int(time.time()):
            raise ValueError("invalid token")
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise ValueError("invalid subject")
        return payload if include_payload else username
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="로그인 정보가 만료되었거나 올바르지 않습니다")

def _get_token_user(cur, authorization: str | None):
    payload = _decode_access_token(authorization, include_payload=True)
    _ensure_admin_tables(cur)
    cur.execute("SELECT u.id, u.username, u.name, COALESCE(m.suspended, 0) AS suspended, COALESCE(m.token_version, 0) AS token_version, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.username = %s", (payload['sub'],))
    user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="계정을 확인할 수 없습니다. 다시 로그인해주세요")
    if user['suspended'] or payload.get('ver', 0) != user['token_version']:
        raise HTTPException(status_code=401, detail="계정 상태가 변경되었습니다. 다시 로그인해주세요")
    return user

_admin_schema_ready = False
_admin_schema_lock = threading.Lock()

def _ensure_admin_tables(cur):
    global _admin_schema_ready
    if _admin_schema_ready:
        return
    with _admin_schema_lock:
        if _admin_schema_ready:
            return
        cur.execute('''CREATE TABLE IF NOT EXISTS admin_user_metadata (
            user_id INT NOT NULL PRIMARY KEY, role VARCHAR(24) NOT NULL DEFAULT 'member',
            suspended TINYINT(1) NOT NULL DEFAULT 0, token_version INT NOT NULL DEFAULT 0,
            registered_at TIMESTAMP NULL DEFAULT NULL,
            CONSTRAINT fk_admin_metadata_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
        cur.execute('''CREATE TABLE IF NOT EXISTS admin_audit_log (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(120) NOT NULL,
            action VARCHAR(40) NOT NULL, target_id INT NULL, target_username VARCHAR(120) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_admin_audit_target (target_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
        _admin_schema_ready = True

def _ensure_user_favorites_table(cur):
    global _favorites_schema_ready
    if _favorites_schema_ready:
        return
    with _favorites_schema_lock:
        if _favorites_schema_ready:
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(120) NOT NULL,
                latitude DECIMAL(10, 7) NOT NULL,
                longitude DECIMAL(10, 7) NOT NULL,
                sort_order TINYINT UNSIGNED NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_favorites_name (user_id, name),
                UNIQUE KEY uq_user_favorites_order (user_id, sort_order),
                CONSTRAINT fk_user_favorites_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        _favorites_schema_ready = True

def _ensure_hiking_tables(cur):
    global _hiking_schema_ready
    if _hiking_schema_ready:
        return
    with _hiking_schema_lock:
        if _hiking_schema_ready:
            return
        statements = [
            """CREATE TABLE IF NOT EXISTS hiking_plans (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(120) NOT NULL,
                mountain_name VARCHAR(120) NOT NULL,
                course_index SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                course_name VARCHAR(180) NOT NULL,
                weather_date VARCHAR(24) NOT NULL DEFAULT '',
                selected_date_idx TINYINT UNSIGNED NOT NULL DEFAULT 0,
                pace VARCHAR(16) NOT NULL DEFAULT 'normal',
                departure_offset_hours SMALLINT NOT NULL DEFAULT 0,
                departure_at VARCHAR(40) NOT NULL DEFAULT '',
                items_json LONGTEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_hiking_plans_user_updated (user_id, updated_at),
                CONSTRAINT fk_hiking_plans_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_parties (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                owner_user_id INT NOT NULL,
                plan_id INT NULL,
                name VARCHAR(120) NOT NULL,
                mountain_name VARCHAR(120) NOT NULL,
                course_name VARCHAR(180) NOT NULL,
                weather_date VARCHAR(24) NOT NULL DEFAULT '',
                pace VARCHAR(16) NOT NULL DEFAULT 'normal',
                departure_offset_hours SMALLINT NOT NULL DEFAULT 0,
                departure_at VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_hiking_parties_owner (owner_user_id),
                CONSTRAINT fk_hiking_parties_owner FOREIGN KEY (owner_user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_hiking_parties_plan FOREIGN KEY (plan_id)
                    REFERENCES hiking_plans(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_members (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                party_id INT NOT NULL,
                user_id INT NOT NULL,
                role VARCHAR(16) NOT NULL DEFAULT 'member',
                joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_hiking_party_member (party_id, user_id),
                CONSTRAINT fk_hiking_party_members_party FOREIGN KEY (party_id)
                    REFERENCES hiking_parties(id) ON DELETE CASCADE,
                CONSTRAINT fk_hiking_party_members_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_invites (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                party_id INT NOT NULL,
                invited_user_id INT NOT NULL,
                invited_by_user_id INT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_hiking_party_invite (party_id, invited_user_id),
                KEY idx_hiking_party_invites_user_status (invited_user_id, status),
                CONSTRAINT fk_hiking_party_invites_party FOREIGN KEY (party_id)
                    REFERENCES hiking_parties(id) ON DELETE CASCADE,
                CONSTRAINT fk_hiking_party_invites_invited FOREIGN KEY (invited_user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_hiking_party_invites_sender FOREIGN KEY (invited_by_user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_items (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                party_id INT NOT NULL,
                name VARCHAR(120) NOT NULL,
                quantity SMALLINT UNSIGNED NOT NULL DEFAULT 1,
                assigned_to_user_id INT NULL,
                is_packed TINYINT(1) NOT NULL DEFAULT 0,
                created_by_user_id INT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_hiking_party_item_name (party_id, name),
                CONSTRAINT fk_hiking_party_items_party FOREIGN KEY (party_id)
                    REFERENCES hiking_parties(id) ON DELETE CASCADE,
                CONSTRAINT fk_hiking_party_items_assignee FOREIGN KEY (assigned_to_user_id)
                    REFERENCES users(id) ON DELETE SET NULL,
                CONSTRAINT fk_hiking_party_items_creator FOREIGN KEY (created_by_user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        ]
        statements.append("""CREATE TABLE IF NOT EXISTS hiking_party_schedule_log (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, party_id INT NOT NULL,
            actor_name VARCHAR(120) NOT NULL, old_schedule TEXT NOT NULL, new_schedule TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_party_schedule_log (party_id, id),
            CONSTRAINT fk_party_schedule_log_party FOREIGN KEY (party_id) REFERENCES hiking_parties(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        for statement in statements:
            cur.execute(statement)
        _hiking_schema_ready = True

@app.post("/api/signup")
def signup(req: SignUpRequest):
    # Do not create accounts that cannot sign in because the token key is missing.
    _auth_signing_key()
    req.username = _validate_username(req.username)
    if len(req.password) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    _validate_password_username(req.username, req.password)

    hashed = _hash_pw(req.password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute(
                "INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                (req.username, hashed, req.name),
            )
            user_id = cur.lastrowid
            cur.execute("INSERT INTO admin_user_metadata (user_id, registered_at) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원가입이 완료되었습니다"}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            cur.execute("SELECT u.*, COALESCE(m.suspended, 0) AS suspended, COALESCE(m.token_version, 0) AS token_version FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.username = %s", (req.username,))
            user = cur.fetchone()
    finally:
        conn.close()

    if not user or len(req.password.encode("utf-8")) > 72 or not bcrypt.checkpw(req.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
    if user['suspended']:
        raise HTTPException(status_code=403, detail="이용이 정지된 계정입니다. 관리자에게 문의해주세요")
    return {
        "message": "로그인 성공",
        "name": user["name"],
        "username": user["username"],
        "access_token": _create_access_token(user["username"], user['token_version']),
        "token_type": "bearer",
        "expires_in": AUTH_TOKEN_TTL_SECONDS,
    }

class FavoriteLocation(BaseModel):
    name: str
    lat: float
    lon: float

@app.get('/api/users/me/role')
def get_my_role(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            return {'role': user['role']}
    finally:
        conn.close()

class ReplaceFavoritesRequest(BaseModel):
    favorites: list[FavoriteLocation]

@app.get("/api/users/me/favorites")
def get_my_favorites(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_user_favorites_table(cur)
            cur.execute(
                "SELECT name, latitude AS lat, longitude AS lon FROM user_favorites WHERE user_id = %s ORDER BY sort_order",
                (user["id"],),
            )
            rows = cur.fetchall()
            return [{"name": row["name"], "lat": float(row["lat"]), "lon": float(row["lon"])} for row in rows]
    finally:
        conn.close()

@app.put("/api/users/me/favorites")
def replace_my_favorites(req: ReplaceFavoritesRequest, authorization: str | None = Header(default=None)):
    if len(req.favorites) > 5:
        raise HTTPException(status_code=400, detail="관심 지역은 최대 5곳까지 저장할 수 있습니다")
    seen_names = set()
    for favorite in req.favorites:
        name = favorite.name.strip()
        if not name or len(name) > 120:
            raise HTTPException(status_code=400, detail="관심 지역 이름을 확인해주세요")
        if not (-90 <= favorite.lat <= 90 and -180 <= favorite.lon <= 180):
            raise HTTPException(status_code=400, detail="관심 지역 좌표를 확인해주세요")
        if name.casefold() in seen_names:
            raise HTTPException(status_code=400, detail="같은 관심 지역이 중복되었습니다")
        seen_names.add(name.casefold())

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_user_favorites_table(cur)
            cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user["id"],))
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user["id"],))
            if req.favorites:
                cur.executemany(
                    "INSERT INTO user_favorites (user_id, name, latitude, longitude, sort_order) VALUES (%s, %s, %s, %s, %s)",
                    [(user["id"], item.name.strip(), item.lat, item.lon, index) for index, item in enumerate(req.favorites)],
                )
        conn.commit()
    finally:
        conn.close()
    return {"message": "관심 지역이 계정에 저장되었습니다", "count": len(req.favorites)}

class HikingPlanItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    checked: bool = False

class HikingPlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mountain_name: str = Field(min_length=1, max_length=120)
    course_index: int = Field(default=0, ge=0, le=99)
    course_name: str = Field(min_length=1, max_length=180)
    weather_date: str = Field(default="", max_length=24)
    selected_date_idx: int = Field(default=0, ge=0, le=30)
    pace: str = "normal"
    departure_offset_hours: int = Field(default=0, ge=0, le=48)
    departure_at: str = Field(default="", max_length=40)
    items: list[HikingPlanItem] = Field(default_factory=list)

class HikingPartyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plan_id: int | None = None
    mountain_name: str = Field(min_length=1, max_length=120)
    course_name: str = Field(min_length=1, max_length=180)
    weather_date: str = Field(default="", max_length=24)
    pace: str = "normal"
    departure_offset_hours: int = Field(default=0, ge=0, le=48)
    departure_at: str = Field(default="", max_length=40)
    items: list[HikingPlanItem] = Field(default_factory=list)

class HikingPartyInviteRequest(BaseModel):
    username: str = Field(min_length=4, max_length=80)

class HikingPartyInviteResponse(BaseModel):
    decision: str

class HikingPartyItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=99)

class HikingPartyItemUpdate(BaseModel):
    quantity: int | None = Field(default=None, ge=1, le=99)
    assigned_to_username: str | None = Field(default=None, max_length=80)
    is_packed: bool | None = None

def _serialize_hiking_plan(row):
    try:
        items = json.loads(row.get("items_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        items = []
    return {
        "id": row["id"],
        "name": row["name"],
        "mountain_name": row["mountain_name"],
        "course_index": int(row.get("course_index") or 0),
        "course_name": row["course_name"],
        "weather_date": row.get("weather_date") or "",
        "selected_date_idx": int(row.get("selected_date_idx") or 0),
        "pace": row.get("pace") or "normal",
        "departure_offset_hours": int(row.get("departure_offset_hours") or 0),
        "departure_at": row.get("departure_at") or "",
        "items": items if isinstance(items, list) else [],
        "updated_at": str(row.get("updated_at") or ""),
    }

def _validate_hiking_plan(req: HikingPlanRequest):
    if req.pace not in {"slow", "normal", "fast"}:
        raise HTTPException(status_code=400, detail="산행 속도를 확인해주세요")
    if any(not value.strip() for value in (req.name, req.mountain_name, req.course_name)):
        raise HTTPException(status_code=400, detail="계획 이름과 산·코스를 입력해 주세요")
    if len(req.items) > 100:
        raise HTTPException(status_code=400, detail="준비물은 계획당 100개까지 저장할 수 있습니다")
    normalized = [item.name.strip() for item in req.items]
    if any(not name for name in normalized) or len({name.casefold() for name in normalized}) != len(normalized):
        raise HTTPException(status_code=400, detail="준비물 이름이 비어 있거나 중복되었습니다")

def _party_member_role(cur, party_id: int, user_id: int):
    cur.execute(
        "SELECT role FROM hiking_party_members WHERE party_id = %s AND user_id = %s",
        (party_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="이 산행 파티의 구성원이 아닙니다")
    return row["role"]

def _get_party_items(cur, party_id: int):
    cur.execute("""
        SELECT i.id, i.name, i.quantity, i.is_packed,
               u.username AS assigned_to_username, u.name AS assigned_to_name,
               creator.username AS created_by_username
        FROM hiking_party_items i
        LEFT JOIN users u ON u.id = i.assigned_to_user_id
        JOIN users creator ON creator.id = i.created_by_user_id
        WHERE i.party_id = %s
        ORDER BY i.id
    """, (party_id,))
    return [
        {
            "id": row["id"], "name": row["name"], "quantity": int(row["quantity"]),
            "is_packed": bool(row["is_packed"]),
            "assigned_to_username": row["assigned_to_username"],
            "assigned_to_name": row["assigned_to_name"],
            "created_by_username": row["created_by_username"],
        }
        for row in cur.fetchall()
    ]

@app.get("/api/users/me/hiking-plans")
def list_my_hiking_plans(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT * FROM hiking_plans WHERE user_id = %s ORDER BY updated_at DESC, id DESC", (user["id"],))
            return [_serialize_hiking_plan(row) for row in cur.fetchall()]
    finally:
        conn.close()

@app.post("/api/users/me/hiking-plans")
def save_hiking_plan(req: HikingPlanRequest, authorization: str | None = Header(default=None)):
    _validate_hiking_plan(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                INSERT INTO hiking_plans
                    (user_id, name, mountain_name, course_index, course_name, weather_date,
                     selected_date_idx, pace, departure_offset_hours, departure_at, items_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["id"], req.name.strip(), req.mountain_name.strip(), req.course_index,
                req.course_name.strip(), req.weather_date, req.selected_date_idx, req.pace,
                req.departure_offset_hours, req.departure_at,
                json.dumps([{"name": item.name.strip(), "checked": item.checked} for item in req.items], ensure_ascii=False),
            ))
            plan_id = cur.lastrowid
        conn.commit()
        return {"id": plan_id, "message": "산행 계획을 계정에 저장했습니다"}
    finally:
        conn.close()

@app.put("/api/users/me/hiking-plans/{plan_id}")
def update_hiking_plan(plan_id: int, req: HikingPlanRequest, authorization: str | None = Header(default=None)):
    _validate_hiking_plan(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                UPDATE hiking_plans SET name = %s, mountain_name = %s, course_index = %s,
                    course_name = %s, weather_date = %s, selected_date_idx = %s, pace = %s,
                    departure_offset_hours = %s, departure_at = %s, items_json = %s
                WHERE id = %s AND user_id = %s
            """, (
                req.name.strip(), req.mountain_name.strip(), req.course_index, req.course_name.strip(),
                req.weather_date, req.selected_date_idx, req.pace, req.departure_offset_hours,
                req.departure_at,
                json.dumps([{"name": item.name.strip(), "checked": item.checked} for item in req.items], ensure_ascii=False),
                plan_id, user["id"],
            ))
            if cur.rowcount == 0:
                cur.execute("SELECT id FROM hiking_plans WHERE id = %s AND user_id = %s", (plan_id, user["id"]))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="저장된 산행 계획을 찾을 수 없습니다")
        conn.commit()
        return {"message": "산행 계획을 업데이트했습니다"}
    finally:
        conn.close()

@app.delete("/api/users/me/hiking-plans/{plan_id}")
def delete_hiking_plan(plan_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("DELETE FROM hiking_plans WHERE id = %s AND user_id = %s", (plan_id, user["id"]))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="저장된 산행 계획을 찾을 수 없습니다")
        conn.commit()
        return {"message": "산행 계획을 삭제했습니다"}
    finally:
        conn.close()

@app.get("/api/hiking/parties/invitations")
def list_hiking_party_invitations(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                SELECT i.id, i.party_id, p.name AS party_name, p.mountain_name, p.course_name,
                       p.weather_date, inviter.username AS invited_by_username, inviter.name AS invited_by_name
                FROM hiking_party_invites i
                JOIN hiking_parties p ON p.id = i.party_id
                JOIN users inviter ON inviter.id = i.invited_by_user_id
                WHERE i.invited_user_id = %s AND i.status = 'pending'
                ORDER BY i.created_at DESC
            """, (user["id"],))
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/hiking/parties/invitations/{invite_id}/respond")
def respond_to_hiking_party_invite(invite_id: int, req: HikingPartyInviteResponse, authorization: str | None = Header(default=None)):
    if req.decision not in {"accept", "decline"}:
        raise HTTPException(status_code=400, detail="초대 응답을 확인해주세요")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT party_id FROM hiking_party_invites WHERE id = %s AND invited_user_id = %s AND status = 'pending' FOR UPDATE", (invite_id, user["id"]))
            invite = cur.fetchone()
            if not invite:
                raise HTTPException(status_code=404, detail="대기 중인 초대를 찾을 수 없습니다")
            new_status = "accepted" if req.decision == "accept" else "declined"
            cur.execute("UPDATE hiking_party_invites SET status = %s WHERE id = %s", (new_status, invite_id))
            if req.decision == "accept":
                cur.execute("INSERT IGNORE INTO hiking_party_members (party_id, user_id, role) VALUES (%s, %s, 'member')", (invite["party_id"], user["id"]))
        conn.commit()
        return {"message": "파티 초대를 수락했습니다" if req.decision == "accept" else "파티 초대를 거절했습니다"}
    finally:
        conn.close()

@app.get("/api/hiking/parties")
def list_hiking_parties(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                SELECT p.id, p.name, p.mountain_name, p.course_name, p.weather_date,
                       p.pace, p.departure_offset_hours, p.departure_at, p.owner_user_id,
                       owner.username AS owner_username, owner.name AS owner_name,
                       mine.role,
                       (SELECT COUNT(*) FROM hiking_party_members all_members WHERE all_members.party_id = p.id) AS member_count
                FROM hiking_parties p
                JOIN hiking_party_members mine ON mine.party_id = p.id AND mine.user_id = %s
                JOIN users owner ON owner.id = p.owner_user_id
                ORDER BY p.updated_at DESC, p.id DESC
            """, (user["id"],))
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/hiking/parties")
def create_hiking_party(req: HikingPartyCreateRequest, authorization: str | None = Header(default=None)):
    if req.pace not in {"slow", "normal", "fast"}:
        raise HTTPException(status_code=400, detail="산행 속도를 확인해주세요")
    if any(not value.strip() for value in (req.name, req.mountain_name, req.course_name)):
        raise HTTPException(status_code=400, detail="계획 이름과 산·코스를 입력해 주세요")
    if len(req.items) > 100:
        raise HTTPException(status_code=400, detail="공동 준비물은 파티당 100개까지 등록할 수 있습니다")
    normalized_items = [item.name.strip() for item in req.items]
    if any(not name for name in normalized_items) or len({name.casefold() for name in normalized_items}) != len(normalized_items):
        raise HTTPException(status_code=400, detail="준비물 이름이 비어 있거나 중복되었습니다")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            if req.plan_id is not None:
                cur.execute("SELECT id FROM hiking_plans WHERE id = %s AND user_id = %s", (req.plan_id, user["id"]))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="연결할 산행 계획을 찾을 수 없습니다")
            cur.execute("""
                INSERT INTO hiking_parties
                    (owner_user_id, plan_id, name, mountain_name, course_name, weather_date, pace, departure_offset_hours, departure_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["id"], req.plan_id, req.name.strip(), req.mountain_name.strip(), req.course_name.strip(),
                req.weather_date, req.pace, req.departure_offset_hours, req.departure_at,
            ))
            party_id = cur.lastrowid
            cur.execute("INSERT INTO hiking_party_members (party_id, user_id, role) VALUES (%s, %s, 'owner')", (party_id, user["id"]))
            if req.items:
                cur.executemany(
                    "INSERT INTO hiking_party_items (party_id, name, quantity, created_by_user_id, is_packed, assigned_to_user_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(party_id, item.name.strip(), 1, user["id"], 1 if item.checked else 0, user["id"] if item.checked else None) for item in req.items],
                )
        conn.commit()
        return {"id": party_id, "message": "산행 파티를 만들었습니다"}
    finally:
        conn.close()

@app.get("/api/hiking/parties/{party_id}")
def get_hiking_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            cur.execute("""
                SELECT p.id, p.name, p.plan_id, p.mountain_name, p.course_name, p.weather_date, p.pace,
                       p.departure_offset_hours, p.departure_at, p.owner_user_id,
                       owner.username AS owner_username, owner.name AS owner_name
                FROM hiking_parties p JOIN users owner ON owner.id = p.owner_user_id WHERE p.id = %s
            """, (party_id,))
            party = cur.fetchone()
            if not party:
                raise HTTPException(status_code=404, detail="산행 파티를 찾을 수 없습니다")
            cur.execute("""
                SELECT u.username, u.name, m.role FROM hiking_party_members m
                JOIN users u ON u.id = m.user_id WHERE m.party_id = %s ORDER BY m.joined_at, u.username
            """, (party_id,))
            members = cur.fetchall()
            pending_invites = []
            if role == "owner":
                cur.execute("""SELECT i.id, u.username, u.name FROM hiking_party_invites i
                    JOIN users u ON u.id = i.invited_user_id
                    WHERE i.party_id = %s AND i.status = 'pending' ORDER BY i.created_at""", (party_id,))
                pending_invites = cur.fetchall()
            cur.execute("SELECT actor_name, old_schedule, new_schedule, created_at FROM hiking_party_schedule_log WHERE party_id = %s ORDER BY id DESC LIMIT 5", (party_id,))
            history = cur.fetchall()
            return {**party, "role": role, "members": members, "items": _get_party_items(cur, party_id), "pending_invites": pending_invites, "schedule_history": history}
    finally:
        conn.close()

@app.post("/api/hiking/parties/{party_id}/invites")
def invite_to_hiking_party(party_id: int, req: HikingPartyInviteRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            inviter = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, inviter["id"])
            if role != "owner":
                raise HTTPException(status_code=403, detail="파티장만 새 구성원을 초대할 수 있습니다")
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username.strip(),))
            invitee = cur.fetchone()
            if not invitee:
                raise HTTPException(status_code=404, detail="해당 아이디를 찾을 수 없습니다")
            if invitee["id"] == inviter["id"]:
                raise HTTPException(status_code=400, detail="본인에게는 초대할 수 없습니다")
            cur.execute("SELECT 1 FROM hiking_party_members WHERE party_id = %s AND user_id = %s", (party_id, invitee["id"]))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 파티 구성원입니다")
            cur.execute("""
                INSERT INTO hiking_party_invites (party_id, invited_user_id, invited_by_user_id, status)
                VALUES (%s, %s, %s, 'pending')
                ON DUPLICATE KEY UPDATE invited_by_user_id = VALUES(invited_by_user_id), status = 'pending', updated_at = CURRENT_TIMESTAMP
            """, (party_id, invitee["id"], inviter["id"]))
        conn.commit()
        return {"message": "파티 초대를 보냈습니다"}
    finally:
        conn.close()

class PartyTransferRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)

class PartyScheduleRequest(BaseModel):
    plan_id: int
    expected_departure_at: str = Field(default="", max_length=40)
    expected_mountain_name: str = Field(default="", max_length=120)
    expected_course_name: str = Field(default="", max_length=180)

@app.post("/api/hiking/parties/{party_id}/schedule")
def update_party_schedule(party_id: int, req: PartyScheduleRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT * FROM hiking_parties WHERE id = %s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party or party['owner_user_id'] != user['id']:
                raise HTTPException(status_code=403, detail="파티장만 일정을 변경할 수 있습니다")
            if (party['departure_at'], party['mountain_name'], party['course_name']) != (req.expected_departure_at, req.expected_mountain_name, req.expected_course_name):
                raise HTTPException(status_code=409, detail="파티 일정이 변경되었습니다. 새로고침 후 다시 확인해 주세요")
            cur.execute("SELECT * FROM hiking_plans WHERE id = %s AND user_id = %s", (req.plan_id, user['id']))
            plan = cur.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="본인의 저장된 계획을 찾을 수 없습니다")
            old = f"{party['mountain_name']} · {party['course_name']} · {party['departure_at']}"
            new = f"{plan['mountain_name']} · {plan['course_name']} · {plan['departure_at']}"
            cur.execute("UPDATE hiking_parties SET plan_id=%s, mountain_name=%s, course_name=%s, weather_date=%s, pace=%s, departure_offset_hours=%s, departure_at=%s WHERE id=%s", (plan['id'], plan['mountain_name'], plan['course_name'], plan['weather_date'], plan['pace'], plan['departure_offset_hours'], plan['departure_at'], party_id))
            cur.execute("INSERT INTO hiking_party_schedule_log (party_id, actor_name, old_schedule, new_schedule) VALUES (%s,%s,%s,%s)", (party_id, user['name'], old, new))
        conn.commit()
        return {"message": "파티 일정을 변경했습니다"}
    finally:
        conn.close()

@app.delete("/api/hiking/parties/{party_id}/invites/{invite_id}")
def cancel_party_invite(party_id: int, invite_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            if _party_member_role(cur, party_id, user['id']) != 'owner':
                raise HTTPException(status_code=403, detail="파티장만 초대를 취소할 수 있습니다")
            cur.execute("UPDATE hiking_party_invites SET status='cancelled' WHERE id=%s AND party_id=%s AND status='pending'", (invite_id, party_id))
            if not cur.rowcount:
                raise HTTPException(status_code=404, detail="대기 중인 초대가 없습니다")
        conn.commit()
        return {"message": "초대를 취소했습니다"}
    finally:
        conn.close()

@app.post("/api/hiking/parties/{party_id}/leave")
def leave_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT id, owner_user_id FROM hiking_parties WHERE id=%s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다")
            if party['owner_user_id'] == user['id']:
                raise HTTPException(status_code=409, detail="파티장은 권한을 넘기거나 파티를 삭제해 주세요")
            _party_member_role(cur, party_id, user['id'])
            cur.execute("UPDATE hiking_party_items SET assigned_to_user_id=NULL, is_packed=0 WHERE party_id=%s AND assigned_to_user_id=%s", (party_id, user['id']))
            cur.execute("UPDATE hiking_party_items SET created_by_user_id=%s WHERE party_id=%s AND created_by_user_id=%s", (party['owner_user_id'], party_id, user['id']))
            cur.execute("DELETE FROM hiking_party_members WHERE party_id=%s AND user_id=%s", (party_id, user['id']))
        conn.commit()
        return {"message": "파티에서 나왔습니다"}
    finally:
        conn.close()

@app.post("/api/hiking/parties/{party_id}/transfer")
def transfer_party(party_id: int, req: PartyTransferRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT owner_user_id FROM hiking_parties WHERE id=%s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party or party['owner_user_id'] != user['id']:
                raise HTTPException(status_code=403, detail="파티장만 권한을 넘길 수 있습니다")
            cur.execute("SELECT u.id FROM users u JOIN hiking_party_members m ON m.user_id=u.id WHERE m.party_id=%s AND u.username=%s", (party_id, req.username.strip()))
            target = cur.fetchone()
            if not target or target['id'] == user['id']:
                raise HTTPException(status_code=400, detail="다른 파티 구성원을 선택해 주세요")
            cur.execute("UPDATE hiking_party_members SET role='member' WHERE party_id=%s AND user_id=%s", (party_id, user['id']))
            cur.execute("UPDATE hiking_party_members SET role='owner' WHERE party_id=%s AND user_id=%s", (party_id, target['id']))
            cur.execute("UPDATE hiking_parties SET owner_user_id=%s, plan_id=NULL WHERE id=%s", (target['id'], party_id))
        conn.commit()
        return {"message": "파티장 권한을 넘겼습니다"}
    finally:
        conn.close()

@app.post("/api/hiking/parties/{party_id}/items")
def add_hiking_party_item(party_id: int, req: HikingPartyItemRequest, authorization: str | None = Header(default=None)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="준비물 이름을 입력해 주세요")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            _party_member_role(cur, party_id, user["id"])
            cur.execute("SELECT COUNT(*) AS count FROM hiking_party_items WHERE party_id = %s", (party_id,))
            if cur.fetchone()["count"] >= 100:
                raise HTTPException(status_code=400, detail="공동 준비물은 파티당 100개까지 등록할 수 있습니다")
            cur.execute("INSERT INTO hiking_party_items (party_id, name, quantity, created_by_user_id) VALUES (%s, %s, %s, %s)",
                        (party_id, req.name.strip(), req.quantity, user["id"]))
            item_id = cur.lastrowid
        conn.commit()
        return {"id": item_id, "message": "준비물을 추가했습니다"}
    finally:
        conn.close()

@app.patch("/api/hiking/parties/{party_id}/items/{item_id}")
def update_hiking_party_item(party_id: int, item_id: int, req: HikingPartyItemUpdate, authorization: str | None = Header(default=None)):
    fields = getattr(req, "model_fields_set", getattr(req, "__fields_set__", set()))
    if not fields:
        raise HTTPException(status_code=400, detail="변경할 항목이 없습니다")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            _party_member_role(cur, party_id, user["id"])
            cur.execute("SELECT id FROM hiking_party_items WHERE id = %s AND party_id = %s", (item_id, party_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="공동 준비물을 찾을 수 없습니다")
            updates = []
            values = []
            if "quantity" in fields and req.quantity is not None:
                updates.append("quantity = %s")
                values.append(req.quantity)
            if "is_packed" in fields and req.is_packed is not None:
                updates.append("is_packed = %s")
                values.append(1 if req.is_packed else 0)
            if "assigned_to_username" in fields:
                assignee_id = None
                if req.assigned_to_username:
                    cur.execute("""
                        SELECT u.id FROM users u JOIN hiking_party_members m ON m.user_id = u.id
                        WHERE m.party_id = %s AND u.username = %s
                    """, (party_id, req.assigned_to_username.strip()))
                    assignee = cur.fetchone()
                    if not assignee:
                        raise HTTPException(status_code=400, detail="담당자는 현재 파티 구성원이어야 합니다")
                    assignee_id = assignee["id"]
                updates.append("assigned_to_user_id = %s")
                values.append(assignee_id)
            if not updates:
                raise HTTPException(status_code=400, detail="변경할 내용을 입력해 주세요")
            if updates:
                cur.execute(f"UPDATE hiking_party_items SET {', '.join(updates)} WHERE id = %s AND party_id = %s", (*values, item_id, party_id))
        conn.commit()
        return {"message": "공동 준비물을 업데이트했습니다"}
    finally:
        conn.close()

@app.delete("/api/hiking/parties/{party_id}/items/{item_id}")
def delete_hiking_party_item(party_id: int, item_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            cur.execute("SELECT created_by_user_id FROM hiking_party_items WHERE id = %s AND party_id = %s", (item_id, party_id))
            item = cur.fetchone()
            if not item:
                raise HTTPException(status_code=404, detail="공동 준비물을 찾을 수 없습니다")
            if role != "owner" and item["created_by_user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="준비물을 추가한 사람 또는 파티장만 삭제할 수 있습니다")
            cur.execute("DELETE FROM hiking_party_items WHERE id = %s", (item_id,))
        conn.commit()
        return {"message": "공동 준비물을 삭제했습니다"}
    finally:
        conn.close()

@app.delete("/api/hiking/parties/{party_id}")
def delete_hiking_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            if role != "owner":
                raise HTTPException(status_code=403, detail="파티장만 파티를 삭제할 수 있습니다")
            cur.execute("DELETE FROM hiking_parties WHERE id = %s", (party_id,))
        conn.commit()
        return {"message": "산행 파티를 삭제했습니다"}
    finally:
        conn.close()

class AIRequest(BaseModel):
    current: dict
    forecast: list
    air_quality: dict | None = None
    mountain_info: dict | None = None

@app.post("/api/weather/ai")
def weather_ai(req: AIRequest):
    ai_data = get_ai_contents(req.current, req.forecast, req.air_quality or {}, req.mountain_info)
    return {"advice": [ai_data["one_line"]], "ai_briefing": ai_data["briefing"]}

def _hash_pw(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise HTTPException(status_code=400, detail="비밀번호가 너무 깁니다")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")

def _verify_user(cur, username: str, password: str):
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    if not user or len(password.encode("utf-8")) > 72 or not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    _ensure_admin_tables(cur)
    cur.execute("SELECT suspended FROM admin_user_metadata WHERE user_id = %s", (user['id'],))
    metadata = cur.fetchone()
    if metadata and metadata['suspended']:
        raise HTTPException(status_code=403, detail="이용이 정지된 계정입니다")
    return user

class UpdateUserRequest(BaseModel):
    username: str
    current_password: str
    name: str
    new_password: str | None = None

class DeleteUserRequest(BaseModel):
    username: str
    password: str

@app.put("/api/users/update")
def update_user(req: UpdateUserRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="이름을 입력해주세요")
    if req.new_password and len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다")
    if req.new_password:
        _validate_password_username(req.username, req.new_password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _verify_user(cur, req.username, req.current_password)
            if req.new_password:
                cur.execute("UPDATE users SET name = %s, password_hash = %s WHERE username = %s",
                            (name, _hash_pw(req.new_password), req.username))
                cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
                uid = cur.fetchone()['id']
                _ensure_admin_tables(cur)
                cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s,1) ON DUPLICATE KEY UPDATE token_version=token_version+1", (uid,))
            else:
                cur.execute("UPDATE users SET name = %s WHERE username = %s", (name, req.username))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다", "name": name}

@app.post("/api/users/delete")
def delete_user(req: DeleteUserRequest):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _verify_user(cur, req.username, req.password)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT 1 FROM hiking_party_members WHERE user_id = %s LIMIT 1", (user['id'],))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="회원 탈퇴 전에 참여 중인 파티에서 나가거나 파티장 권한을 넘겨 주세요")
            cur.execute("DELETE FROM users WHERE username = %s", (req.username,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "탈퇴되었습니다"}

class ResetPasswordRequest(BaseModel):
    username: str
    current_password: str = Field(min_length=4, max_length=72)
    new_password: str

@app.post("/api/password/reset")
def reset_password(req: ResetPasswordRequest):
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다")
    _validate_password_username(req.username, req.new_password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _verify_user(cur, req.username, req.current_password)
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                        (_hash_pw(req.new_password), user["id"]))
            _ensure_admin_tables(cur)
            cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s,1) ON DUPLICATE KEY UPDATE token_version=token_version+1", (user['id'],))
        conn.commit()
    finally:
        conn.close()
    return {"message": "비밀번호가 변경되었습니다"}

def require_admin(x_admin_password: str | None, authorization: str | None = None, scope: str = 'admin'):
    if authorization:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                user = _get_token_user(cur, authorization)
                allowed = {'superadmin': {'admin', 'members', 'notices', 'system'}, 'member_manager': {'admin', 'members'}, 'notice_manager': {'admin', 'notices'}}
                if scope not in allowed.get(user['role'], set()):
                    raise HTTPException(status_code=403, detail='관리자 권한이 없습니다')
                return {'name': user['username'], 'role': user['role']}
        finally:
            conn.close()
    expected = os.getenv("ADMIN_PASSWORD")
    if (not expected or not x_admin_password
            or not hmac.compare_digest(x_admin_password.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="관리자 인증에 실패했습니다")
    return {'name': '공용 관리자 비밀번호', 'role': 'superadmin'}

class AdminUserUpdate(BaseModel):
    username: str
    name: str
    password: str | None = None

class AdminUserState(BaseModel):
    suspended: bool | None = None
    role: str | None = None

def _require_manage_target(cur, actor, user_id):
    cur.execute("SELECT u.username, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.id = %s FOR UPDATE", (user_id,))
    target = cur.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail='회원을 찾을 수 없습니다')
    if actor['role'] != 'superadmin' and target['role'] != 'member':
        raise HTTPException(status_code=403, detail='관리자 계정은 최고 관리자만 관리할 수 있습니다')
    return target

@app.post("/api/admin/verify")
def admin_verify(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    return {"ok": True, "role": actor['role']}

@app.post("/api/admin/integrations/marine/check")
def admin_check_marine_integrations(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    """Privileged, secret-safe live connectivity test for configured marine APIs."""
    require_admin(x_admin_password, authorization, 'system')
    return check_marine_integrations()

@app.get("/api/admin/users")
def admin_list_users(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            cur.execute("SHOW COLUMNS FROM users LIKE 'created_at'")
            has_created_at = bool(cur.fetchone())
            date_expr = "COALESCE(u.created_at, m.registered_at)" if has_created_at else "m.registered_at"
            cur.execute("SELECT u.id, u.username, u.name, " + date_expr + " AS created_at, COALESCE(m.suspended, 0) AS suspended, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id ORDER BY u.id")
            return cur.fetchall()
    finally:
        conn.close()

@app.get("/api/admin/users/{user_id}/summary")
def admin_user_summary(user_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, name FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")
            _ensure_hiking_tables(cur)
            cur.execute("SELECT COUNT(*) AS total FROM hiking_plans WHERE user_id = %s", (user_id,))
            plans = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM hiking_party_members WHERE user_id = %s", (user_id,))
            parties = cur.fetchone()["total"]
            return {**user, "plan_count": plans, "party_count": parties}
    finally:
        conn.close()

@app.post("/api/admin/users")
def admin_create_user(req: SignUpRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    req.username = _validate_username(req.username)
    if len(req.password) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    _validate_password_username(req.username, req.password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute("INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                        (req.username, _hash_pw(req.password), req.name.strip()))
            user_id = cur.lastrowid
            cur.execute("INSERT INTO admin_user_metadata (user_id, registered_at) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, 'create', %s, %s)", (actor['name'], user_id, req.username))
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원이 추가되었습니다"}

@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, req: AdminUserUpdate, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    req.username = _validate_username(req.username)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    if req.password and len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")
    if req.password:
        _validate_password_username(req.username, req.password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            target = _require_manage_target(cur, actor, user_id)
            if req.username != target['username'] and not req.password:
                raise HTTPException(status_code=400, detail='아이디를 변경할 때 새 비밀번호도 입력해주세요')
            cur.execute("SELECT id FROM users WHERE username = %s AND id <> %s", (req.username, user_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            if req.password:
                cur.execute("UPDATE users SET username = %s, name = %s, password_hash = %s WHERE id = %s",
                            (req.username, req.name.strip(), _hash_pw(req.password), user_id))
            else:
                cur.execute("UPDATE users SET username = %s, name = %s WHERE id = %s",
                            (req.username, req.name.strip(), user_id))
            if req.password or req.username != target['username']:
                cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s, 1) ON DUPLICATE KEY UPDATE token_version = token_version + 1", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)", (actor['name'], 'update_password' if req.password else 'update', user_id, target['username']))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다"}

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            target = _require_manage_target(cur, actor, user_id)
            if target['username'] == actor['name']:
                raise HTTPException(status_code=400, detail='현재 관리자 계정은 삭제할 수 없습니다')
            _ensure_user_favorites_table(cur)
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, 'delete', %s, %s)", (actor['name'], user_id, target['username']))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}

@app.patch('/api/admin/users/{user_id}/state')
def admin_user_state(user_id: int, req: AdminUserState, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    if req.suspended is None and req.role is None:
        raise HTTPException(status_code=400, detail='변경할 상태를 선택해주세요')
    if req.role is not None and (actor['role'] != 'superadmin' or req.role not in ('member', 'member_manager', 'notice_manager', 'superadmin')):
        raise HTTPException(status_code=403, detail='권한 변경은 최고 관리자만 할 수 있습니다')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            target = _require_manage_target(cur, actor, user_id)
            if target['username'] == actor['name']:
                raise HTTPException(status_code=400, detail='현재 관리자 계정의 상태나 권한은 변경할 수 없습니다')
            cur.execute('INSERT INTO admin_user_metadata (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id', (user_id,))
            if req.suspended is not None:
                cur.execute('UPDATE admin_user_metadata SET suspended = %s, token_version = token_version + 1 WHERE user_id = %s', (req.suspended, user_id))
                cur.execute('INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)', (actor['name'], 'suspend' if req.suspended else 'restore', user_id, target['username']))
            if req.role is not None:
                cur.execute('UPDATE admin_user_metadata SET role = %s WHERE user_id = %s', (req.role, user_id))
                cur.execute('INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)', (actor['name'], 'role:' + req.role, user_id, target['username']))
        conn.commit()
        return {'message': '회원 상태가 변경되었습니다'}
    finally:
        conn.close()

@app.get('/api/admin/audit')
def admin_audit(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            cur.execute('SELECT id, actor, action, target_username, created_at FROM admin_audit_log ORDER BY id DESC LIMIT 100')
            return cur.fetchall()
    finally:
        conn.close()


# ======================= 공지사항 (MySQL) =======================
# 읽기: 누구나 / 작성·수정·삭제: 관리자(x-admin-password)만
from datetime import datetime as _dt

_NOTICE_TYPES = ("안내", "점검", "긴급")
_notices_schema_ready = False
_notices_schema_lock = threading.Lock()

def _ensure_notices_table(cur):
    global _notices_schema_ready
    if _notices_schema_ready:
        return
    with _notices_schema_lock:
        if _notices_schema_ready:
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                notice_type VARCHAR(10)  NOT NULL DEFAULT '안내',
                title       VARCHAR(200) NOT NULL,
                notice_date DATE         NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        _notices_schema_ready = True

class NoticeRequest(BaseModel):
    type: str = "안내"
    title: str
    date: str  # YYYY.MM.DD

def _parse_notice(req: NoticeRequest):
    title = req.title.strip()
    if not title or len(title) > 200:
        raise HTTPException(status_code=400, detail="제목은 1~200자로 입력해주세요")
    if req.type not in _NOTICE_TYPES:
        raise HTTPException(status_code=400, detail="분류가 올바르지 않습니다")
    try:
        d = _dt.strptime(req.date.strip(), "%Y.%m.%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜는 YYYY.MM.DD 형식으로 입력해주세요")
    return req.type, title, d

def _serialize_notice(row):
    return {
        "id": row["id"],
        "type": row["notice_type"],
        "title": row["title"],
        "date": row["notice_date"].strftime("%Y.%m.%d"),
        "color": "#e53e3e" if row["notice_type"] == "긴급" else "#3182ce",
    }

@app.get("/api/notices")
def list_notices():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("SELECT id, notice_type, title, notice_date FROM notices ORDER BY notice_date DESC, id DESC")
            return [_serialize_notice(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.post("/api/admin/notices")
def admin_create_notice(req: NoticeRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    ntype, title, d = _parse_notice(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("INSERT INTO notices (notice_type, title, notice_date) VALUES (%s, %s, %s)", (ntype, title, d))
            new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"message": "공지사항이 등록되었습니다", "id": new_id}

@app.put("/api/admin/notices/{notice_id}")
def admin_update_notice(notice_id: int, req: NoticeRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    ntype, title, d = _parse_notice(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("SELECT id FROM notices WHERE id = %s", (notice_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다")
            cur.execute("UPDATE notices SET notice_type = %s, title = %s, notice_date = %s WHERE id = %s",
                        (ntype, title, d, notice_id))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다"}

@app.delete("/api/admin/notices/{notice_id}")
def admin_delete_notice(notice_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}
