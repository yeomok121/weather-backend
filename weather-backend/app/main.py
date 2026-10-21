from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import concurrent.futures
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

# 💡 프론트엔드에서 데이터를 넘겨받기 위한 데이터 구조
class AIDataRequest(BaseModel):
    current: dict
    forecast: list
    air_quality: dict

@app.get("/")
def hello():
    return {"message": "날씨 서버 작동 중!"}

@app.get("/api/weather/summary")
def summary(lat: float, lon: float):
    try:
        # 💡 병렬 처리(ThreadPoolExecutor)로 4개의 API를 동시에 호출하여 수집 시간을 1/4로 단축!
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

# 💡 AI 데이터만 따로 요청받는 전용 엔드포인트 생성
@app.post("/api/weather/ai")
def ai_summary(data: AIDataRequest):
    try:
        ai_data = get_ai_contents(data.current, data.forecast, data.air_quality)
        return {
            "advice": [ai_data["one_line"]],
            "ai_briefing": ai_data["briefing"]
        }
    except Exception:
        return {
            "advice": ["AI 멘트를 불러오지 못했습니다."],
            "ai_briefing": "AI 날씨 전망을 불러오지 못했습니다."
        }

@app.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r