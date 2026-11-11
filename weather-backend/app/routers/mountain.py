"""산악 페이지 API: 산 목록, 산 검색, 산악 기상 요약."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.weather import (
    search_mountain, get_mountain_weather,
    MOUNTAINS_DB, refresh_mountain_catalog, _mountain_catalog_diagnostics,
)

router = APIRouter()


@router.get("/api/mountain/list")
def api_mountain_list():
    refresh_mountain_catalog()
    return [
        {"name": name, **info}
        for name, info in list(MOUNTAINS_DB.items())
    ]

@router.get("/api/mountain/catalog/status")
def api_mountain_catalog_status():
    refresh_mountain_catalog()
    return {"catalog_count": len(MOUNTAINS_DB), "source_check": dict(_mountain_catalog_diagnostics)}

@router.get("/api/mountain/search")
def api_search_mountain(q: str):
    result = search_mountain(q)
    if not result:
        raise HTTPException(status_code=404, detail="해당 산을 찾을 수 없습니다.")
    return result

@router.get("/api/mountain/summary")
def api_mountain_summary(name: str):
    try:
        result = get_mountain_weather(name)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="기상 공급 기관에서 산악 예보를 받지 못했습니다. 잠시 후 다시 확인해 주세요.") from None
    if not result:
        raise HTTPException(status_code=404, detail="산 정보를 불러올 수 없습니다.")
    result["metadata"] = {"generated_at": datetime.now(timezone.utc).isoformat(), "degraded": not result.get("hourly")}
    return result
