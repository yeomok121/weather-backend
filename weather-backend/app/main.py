from fastapi import FastAPI, HTTPException
import requests
from app.advice import make_advice
from app.weather import get_current_weather, get_hourly_forecast, search_location, get_air_quality
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 💡 세상 어디서든(Vercel 포함) 내 백엔드에 접근할 수 있도록 모든 도메인 허용("*")으로 변경
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def hello():
    return {"message": "날씨 서버 작동 중!"}

@app.get("/api/weather/current")
def current(lat: float, lon: float):
    try:
        return get_current_weather(lat, lon)
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="날씨 서버 호출에 실패했습니다")
    
@app.get("/api/weather/hourly")
def hourly(lat: float, lon: float):
    try:
        return get_hourly_forecast(lat, lon)
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="예보 호출에 실패했습니다")

@app.get("/api/weather/summary")
def summary(lat: float, lon: float):
    """메인 화면용: 현재 날씨 + 시간별 예보 + 알림 문구 + 대기질(미세먼지) 데이터를 한 번에 반환합니다."""
    try:
        current = get_current_weather(lat, lon)
        forecast = get_hourly_forecast(lat, lon)
        air_quality = get_air_quality(lat, lon) 
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="날씨/대기질 호출에 실패했습니다")
    
    return {
        "current": current,
        "hourly": forecast,
        "advice": make_advice(current, forecast),
        "air_quality": air_quality
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r