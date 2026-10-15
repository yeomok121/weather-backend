from fastapi import FastAPI, HTTPException
import requests
from app.advice import get_ai_contents
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
    
    # 💡 AI에서 한 줄 멘트와 상세 브리핑을 동시에 받아옴
    ai_data = get_ai_contents(current, forecast, air_quality)
    
    return {
        "current": current,
        "hourly": forecast,
        "advice": [ai_data["one_line"]],       # 메인 카드 한 줄 멘트 (AI 생성)
        "ai_briefing": ai_data["briefing"],    # 하단 탭 상세 브리핑 (AI 생성)
        "air_quality": air_quality,
        "weekly": weekly
    }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r