from fastapi import FastAPI, HTTPException
import requests
from app.advice import make_advice, get_ai_briefing # 💡 가져오기
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

@app.get("/api/weather/summary")
def summary(lat: float, lon: float):
    try:
        current = get_current_weather(lat, lon)
        forecast = get_hourly_forecast(lat, lon)
        air_quality = get_air_quality(lat, lon) 
        weekly = get_weekly_forecast(lat, lon)
    except Exception:
        raise HTTPException(status_code=502, detail="서버 오류")
    
    return {
        "current": current,
        "hourly": forecast,
        "advice": make_advice(current, forecast),
        "ai_briefing": get_ai_briefing(current, air_quality), # 💡 AI 브리핑 데이터를 응답에 추가!
        "air_quality": air_quality,
        "weekly": weekly
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r