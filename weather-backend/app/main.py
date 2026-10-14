from fastapi import FastAPI, HTTPException
import requests
from app.advice import make_advice
from app.weather import get_current_weather, get_hourly_forecast, search_location, get_air_quality, get_weekly_forecast
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
    try:
        current = get_current_weather(lat, lon)
        forecast = get_hourly_forecast(lat, lon)
        air_quality = get_air_quality(lat, lon) 
        weekly = get_weekly_forecast(lat, lon) # 💡 주간 예보 데이터 수집
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="날씨/대기질 호출에 실패했습니다")
    
    return {
        "current": current,
        "hourly": forecast,
        "advice": make_advice(current, forecast),
        "air_quality": air_quality,
        "weekly": weekly # 💡 프론트로 전달
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r