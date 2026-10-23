import os
import hmac
import concurrent.futures
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
import requests
from app.advice import get_ai_contents
from app.weather import get_current_weather, get_hourly_forecast, search_location, get_air_quality, get_weekly_forecast
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
        # 💡 4개의 날씨 API를 동시에 병렬 호출하여 대기 시간을 1/4로 단축!
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

class SignUpRequest(BaseModel):
    username: str
    password: str
    name: str

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/signup")
def signup(req: SignUpRequest):
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
    return {"message": "로그인 성공", "name": user["name"], "username": user["username"]}

# ---------------- AI 멘트 (프론트가 따로 호출) ----------------
class AIRequest(BaseModel):
    current: dict
    forecast: list
    air_quality: dict | None = None

@app.post("/api/weather/ai")
def weather_ai(req: AIRequest):
    ai_data = get_ai_contents(req.current, req.forecast, req.air_quality or {})
    return {"advice": [ai_data["one_line"]], "ai_briefing": ai_data["briefing"]}

# ---------------- 공통 도우미 ----------------
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

# ---------------- 내 정보 수정 / 회원 탈퇴 ----------------
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

# ---------------- 비밀번호 찾기 (아이디 + 이름 확인 후 재설정) ----------------
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

# ---------------- 관리자 API (헤더 x-admin-password 필요) ----------------
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
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}