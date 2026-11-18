"""낚시 페이지 API: 해역·포인트 목록, 바다 낚시 정보 요약, 즐겨찾는 포인트."""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.fishing import get_fishing_areas, get_fishing_summary
from app.fishing.service import get_fishing_points, get_point_index, summary_for_point
from app.fishing.areas import get_area
from app.fishing import favorites
from app.core.auth import _get_token_user
from app.database import get_conn

router = APIRouter()


@router.get("/api/fishing/areas")
def api_fishing_areas():
    """화면에서 고를 수 있는 해역 목록 (app/fishing/areas.py)."""
    return get_fishing_areas()


@router.get("/api/fishing/points")
def api_fishing_points():
    """바다낚시지수 낚시 포인트 전체(지도·목록 고르기용)와 연결된 바다 구역 (app/fishing/zones.py)."""
    try:
        return get_fishing_points()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="낚시 포인트 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/api/fishing/points/{point_id}/index")
def api_fishing_point_index(point_id: str):
    """포인트 하나의 바다낚시지수 자세히 (지수 설명 창용)."""
    try:
        result = get_point_index(point_id.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="바다낚시지수를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
    if result is None:
        raise HTTPException(status_code=404, detail="지원하지 않는 낚시 포인트입니다.")
    return result


@router.get("/api/fishing/summary")
def api_fishing_summary(place: str = "추자도 해상", region: str = "", lat: float = 33.96194, lon: float = 126.44527, area: str = "", point: str = ""):
    """Public fishing dashboard data from approved KHOA/KMA marine APIs.

    point(낚시 포인트 이름)를 주면 그 포인트 위치와 연결된 바다 구역을 씁니다.
    area(해역 id)를 주면 해역 목록의 이름·예보 구역·좌표를 씁니다.
    예전 방식(place, region, lat, lon 직접 전달)도 그대로 동작합니다.
    """
    try:
        if point:
            result = summary_for_point(point.strip())
            if result is None:
                raise HTTPException(status_code=404, detail="지원하지 않는 낚시 포인트입니다.")
            return result
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


class FishingPointsRequest(BaseModel):
    points: list[str]


@router.get("/api/users/me/fishing-points")
def get_my_fishing_points(authorization: str | None = Header(default=None)):
    """로그인한 사용자의 즐겨찾는 낚시 포인트 이름 목록 (저장한 순서)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            return {"points": favorites.get_points(cur, user["id"])}
    finally:
        conn.close()


@router.put("/api/users/me/fishing-points")
def replace_my_fishing_points(req: FishingPointsRequest, authorization: str | None = Header(default=None)):
    try:
        names = favorites.clean_names(req.points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            favorites.replace_points(cur, user["id"], names)
        conn.commit()
    finally:
        conn.close()
    return {"message": "즐겨찾는 낚시 포인트를 계정에 저장했습니다", "points": names}
