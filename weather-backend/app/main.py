from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import bcrypt
from app.advice import get_ai_contents
from app.weather import get_current_weather, get_hourly_forecast, search_location, get_air_quality, get_weekly_forecast
from app.database import get_conn
from fastapi.middleware.cors import CORSMiddleware

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
        current = get_current_weather(lat, lon)
        forecast = get_hourly_forecast(lat, lon)
        air_quality = get_air_quality(lat, lon)
        weekly = get_weekly_forecast(lat, lon)
    except Exception:
        raise HTTPException(status_code=502, detail="서버 오류")

    # 💡 AI에서 한 줄 멘트와 상세 브리핑을 동시에 받아옴
    ai_data = get_ai_contents(current, forecast, air_quality)

    return {
        "current": current,
        "hourly": forecast,
        "advice": [ai_data["one_line"]],
        "ai_briefing": ai_data["briefing"],
        "air_quality": air_quality,
        "weekly": weekly
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r

# ---------------- 회원가입 / 로그인 (MySQL) ----------------
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