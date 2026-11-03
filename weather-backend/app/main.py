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
from pydantic import BaseModel
import bcrypt
from app.database import get_conn

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
