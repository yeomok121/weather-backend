"""낚시 페이지 API: 해역 목록, 바다 낚시 정보 요약."""
from fastapi import APIRouter, HTTPException

from app.fishing import get_fishing_areas, get_fishing_summary
from app.fishing.areas import get_area

router = APIRouter()


@router.get("/api/fishing/areas")
def api_fishing_areas():
    """화면에서 고를 수 있는 해역 목록 (app/fishing/areas.py)."""
    return get_fishing_areas()


@router.get("/api/fishing/summary")
def api_fishing_summary(place: str = "추자도 해상", region: str = "", lat: float = 33.96194, lon: float = 126.44527, area: str = ""):
    """Public fishing dashboard data from approved KHOA/KMA marine APIs.

    area(해역 id)를 주면 해역 목록의 이름·예보 구역·좌표를 씁니다.
    예전 방식(place, region, lat, lon 직접 전달)도 그대로 동작합니다.
    """
    try:
        if area:
            selected = get_area(area)
            if selected is None:
                raise HTTPException(status_code=404, detail="지원하지 않는 해역입니다.")
            place, region, lat, lon = selected["place"], selected["region"], selected["lat"], selected["lon"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HTTPException(status_code=422, detail="위치 좌표 범위를 확인해 주세요.")
        return get_fishing_summary(place=place, region=region, lat=lat, lon=lon, area_id=area)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="해양 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
