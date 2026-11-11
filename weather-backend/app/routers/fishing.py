"""낚시 페이지 API: 바다 낚시 정보 요약."""
from fastapi import APIRouter, HTTPException

from app.fishing import get_fishing_summary

router = APIRouter()


@router.get("/api/fishing/summary")
def api_fishing_summary(place: str = "추자도 해상", region: str = "", lat: float = 33.96194, lon: float = 126.44527):
    """Public fishing dashboard data from approved KHOA/KMA marine APIs."""
    try:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(status_code=422, detail="위치 좌표 범위를 확인해 주세요.")
        return get_fishing_summary(place=place, region=region, lat=lat, lon=lon)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="해양 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
