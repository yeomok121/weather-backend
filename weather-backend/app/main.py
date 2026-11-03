import os
import hmac
import base64
import json
import time
import threading
import concurrent.futures
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
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
    MOUNTAINS_DB
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import bcrypt
from app.database import get_conn
from app.marine_integrations import check_marine_integrations
from app.fishing import get_fishing_summary

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def hello():
    return {"message": "날씨 서버 작동 중!"}

@app.get("/api/weather/summary")
def summary(lat: float, lon: float):
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            f_current = executor.submit(get_current_weather, lat, lon)
            f_hourly = executor.submit(get_hourly_forecast, lat, lon)
            f_aqi = executor.submit(get_air_quality, lat, lon)
            f_weekly = executor.submit(get_weekly_forecast, lat, lon)

            current = f_current.result()
            forecast = f_hourly.result()
            air_quality = f_aqi.result()
            weekly = f_weekly.result()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"서버 오류: {str(e)}")
    
    return {
        "current": current,
        "hourly": forecast,
        "air_quality": air_quality,
        "weekly": weekly
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
    return [
        {"name": name, **info}
        for name, info in MOUNTAINS_DB.items()
    ]

@app.get("/api/mountain/search")
def api_search_mountain(q: str):
    result = search_mountain(q)
    if not result:
        raise HTTPException(status_code=404, detail="해당 산을 찾을 수 없습니다.")
    return result

@app.get("/api/mountain/summary")
def api_mountain_summary(name: str):
    result = get_mountain_weather(name)
    if not result:
        raise HTTPException(status_code=404, detail="산 정보를 불러올 수 없습니다.")
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

def _create_access_token(username: str) -> str:
    payload = json.dumps({"sub": username, "exp": int(time.time()) + AUTH_TOKEN_TTL_SECONDS}, separators=(",", ":")).encode("utf-8")
    encoded_payload = _encode_token_part(payload)
    signature = hmac.new(_auth_signing_key(), encoded_payload.encode("ascii"), "sha256").digest()
    return f"{encoded_payload}.{_encode_token_part(signature)}"

def _decode_access_token(authorization: str | None) -> str:
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
        return username
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="로그인 정보가 만료되었거나 올바르지 않습니다")

def _get_token_user(cur, authorization: str | None):
    username = _decode_access_token(authorization)
    cur.execute("SELECT id, username, name FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="계정을 확인할 수 없습니다. 다시 로그인해주세요")
    return user

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
        for statement in statements:
            cur.execute(statement)
        _hiking_schema_ready = True

@app.post("/api/signup")
def signup(req: SignUpRequest):
    # Do not create accounts that cannot sign in because the token key is missing.
    _auth_signing_key()
    if len(req.username) < 4 or len(req.password) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")

    hashed = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute(
                "INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                (req.username, hashed, req.name),
            )
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원가입이 완료되었습니다"}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (req.username,))
            user = cur.fetchone()
    finally:
        conn.close()

    if not user or not bcrypt.checkpw(req.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
    return {
        "message": "로그인 성공",
        "name": user["name"],
        "username": user["username"],
        "access_token": _create_access_token(user["username"]),
        "token_type": "bearer",
        "expires_in": AUTH_TOKEN_TTL_SECONDS,
    }

class FavoriteLocation(BaseModel):
    name: str
    lat: float
    lon: float

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
                    "INSERT INTO hiking_party_items (party_id, name, quantity, created_by_user_id) VALUES (%s, %s, %s, %s)",
                    [(party_id, item.name.strip(), 1, user["id"]) for item in req.items],
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
                SELECT p.id, p.name, p.mountain_name, p.course_name, p.weather_date, p.pace,
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
            return {**party, "role": role, "members": members, "items": _get_party_items(cur, party_id)}
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

@app.post("/api/hiking/parties/{party_id}/items")
def add_hiking_party_item(party_id: int, req: HikingPartyItemRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            _party_member_role(cur, party_id, user["id"])
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
    if not user or not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
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

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _verify_user(cur, req.username, req.current_password)
            if req.new_password:
                cur.execute("UPDATE users SET name = %s, password_hash = %s WHERE username = %s",
                            (name, _hash_pw(req.new_password), req.username))
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
            _verify_user(cur, req.username, req.password)
            cur.execute("DELETE FROM users WHERE username = %s", (req.username,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "탈퇴되었습니다"}

class ResetPasswordRequest(BaseModel):
    username: str
    name: str
    new_password: str

@app.post("/api/password/reset")
def reset_password(req: ResetPasswordRequest):
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s AND name = %s",
                        (req.username, req.name.strip()))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="일치하는 회원 정보가 없습니다")
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                        (_hash_pw(req.new_password), user["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"message": "비밀번호가 변경되었습니다"}

def require_admin(x_admin_password: str | None):
    expected = os.getenv("ADMIN_PASSWORD")
    if (not expected or not x_admin_password
            or not hmac.compare_digest(x_admin_password.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="관리자 인증에 실패했습니다")

class AdminUserUpdate(BaseModel):
    username: str
    name: str
    password: str | None = None

@app.post("/api/admin/verify")
def admin_verify(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return {"ok": True}

@app.post("/api/admin/integrations/marine/check")
def admin_check_marine_integrations(x_admin_password: str | None = Header(default=None)):
    """Privileged, secret-safe live connectivity test for configured marine APIs."""
    require_admin(x_admin_password)
    return check_marine_integrations()

@app.get("/api/admin/users")
def admin_list_users(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, name FROM users ORDER BY id")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/admin/users")
def admin_create_user(req: SignUpRequest, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    if len(req.username) < 4 or len(req.password) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute("INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                        (req.username, _hash_pw(req.password), req.name.strip()))
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원이 추가되었습니다"}

@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, req: AdminUserUpdate, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    if len(req.username) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    if req.password and len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s AND id <> %s", (req.username, user_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            if req.password:
                cur.execute("UPDATE users SET username = %s, name = %s, password_hash = %s WHERE id = %s",
                            (req.username, req.name.strip(), _hash_pw(req.password), user_id))
            else:
                cur.execute("UPDATE users SET username = %s, name = %s WHERE id = %s",
                            (req.username, req.name.strip(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다"}

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_user_favorites_table(cur)
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}
