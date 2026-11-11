"""날씨 페이지 API: 도시 날씨 요약, 지역 검색, AI 안내."""
import concurrent.futures
from datetime import datetime, timezone
from contextvars import copy_context

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.advice import get_ai_contents
from app.weather import (
    get_current_weather, get_hourly_forecast, search_location,
    get_air_quality, get_weekly_forecast,
)
from app.weather_diagnostics import run_weather_task, log_error, request_id_var

router = APIRouter()


@router.get("/api/weather/summary")
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

@router.get("/api/location/search")
def location(q: str):
    r = search_location(q)
    if r is None:
        raise HTTPException(status_code=404, detail="지역을 찾을 수 없습니다")
    return r

class AIRequest(BaseModel):
    current: dict
    forecast: list
    air_quality: dict | None = None
    mountain_info: dict | None = None

@router.post("/api/weather/ai")
def weather_ai(req: AIRequest):
    ai_data = get_ai_contents(req.current, req.forecast, req.air_quality or {}, req.mountain_info)
    return {"advice": [ai_data["one_line"]], "ai_briefing": ai_data["briefing"]}
