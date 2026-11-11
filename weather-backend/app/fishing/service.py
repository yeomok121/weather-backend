"""낚시 페이지: 화면에 보낼 바다 정보 요약을 만듭니다.

- 기관 API 3곳(해상예보, 부이 관측, 조위)을 동시에 불러 기다리는 시간을 줄입니다.
- 결과를 잠시 캐시해 같은 해역을 여러 사람이 봐도 기관 API를 반복 호출하지 않습니다.
- 한 기관이 실패해도 나머지 자료는 보여주고, 실패한 항목은 errors에 사용자용 문구로 담습니다.
"""

import concurrent.futures
from contextvars import copy_context
from datetime import datetime, timedelta

import requests

from app.fishing import providers
from app.fishing.areas import FISHING_AREAS, REGION_NAMES
from app.fishing.cache import TTLCache
from app.fishing.tides import find_extremes, normalize_predictions

# 자료별 보관 시간(초). 기관 갱신 주기보다 짧게 잡았습니다.
FORECAST_TTL_SECONDS = 10 * 60
BUOY_TTL_SECONDS = 5 * 60
TIDE_TTL_SECONDS = 30 * 60

cache = TTLCache()


def get_fishing_areas() -> list[dict]:
    return [dict(area) for area in FISHING_AREAS]


def _resolve_region(region: str) -> tuple[str, str]:
    """예보 구역 코드가 비어 있으면 제주 해역을 찾아 씁니다 (기존 동작 유지)."""
    if region:
        return region, REGION_NAMES.get(region, "")
    try:
        available_regions = providers.get_fishing_regions()
    except requests.RequestException:
        # The region catalog is optional; use the verified Jeju-wide zone if unavailable.
        available_regions = []
    matched = next((item for item in available_regions if "제주" in item.get("REG_NAME", "") and "앞바다" in item.get("REG_NAME", "")), None)
    if matched is None:
        matched = next((item for item in available_regions if "제주" in item.get("REG_NAME", "")), None)
    if matched is None:
        # KMA zone list formats can change; this is the official Jeju-wide short-term zone.
        return "12D00000", "제주도 전해상"
    return matched["REG_ID"], matched.get("REG_NAME", "")


def _load_forecast(region: str, now: datetime) -> list[dict]:
    rows, _ = cache.get_or_load(("forecast", region), FORECAST_TTL_SECONDS,
                                lambda: providers.fetch_marine_forecast(region, now))
    current_hour = now.strftime("%Y%m%d%H")
    return [dict(row) for row in rows if row.get("TM_EF", "") >= current_hour]


def _load_buoys(lat: float, lon: float) -> list[dict]:
    rows, _ = cache.get_or_load(("buoy",), BUOY_TTL_SECONDS, providers.fetch_buoy_observations)
    buoy = []
    for source in rows:
        row = dict(source)
        try:
            row["distance_km"] = providers.distance_km(lat, lon, float(row["LAT"]), float(row["LON"]))
        except (KeyError, TypeError, ValueError):
            row["distance_km"] = None
        buoy.append(row)
    buoy.sort(key=lambda row: row["distance_km"] if row["distance_km"] is not None else float("inf"))
    return buoy[:8]


def _load_tide(lat: float, lon: float, date: str):
    def load():
        data = providers.fetch_tide_prediction(lat, lon, date)
        if data is None:
            raise ValueError("unreadable tide response")  # 읽지 못한 응답은 캐시하지 않습니다.
        return data
    data, _ = cache.get_or_load(("tide", date, round(lat, 4), round(lon, 4)), TIDE_TTL_SECONDS, load)
    return data


def get_fishing_summary(place: str, region: str, lat: float = 33.96194, lon: float = 126.44527, area_id: str = "") -> dict:
    now = datetime.now(providers.SEOUL)
    if not providers.kma_key() or not providers.tidebed_key():
        raise RuntimeError("해양 예보 또는 조위 API 설정이 아직 완료되지 않았습니다.")

    errors = {}
    region_name = REGION_NAMES.get(region, "")

    def forecast_task():
        resolved_region, resolved_name = _resolve_region(region)
        return resolved_region, resolved_name, _load_forecast(resolved_region, now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        forecast_future = executor.submit(copy_context().run, forecast_task)
        buoy_future = executor.submit(copy_context().run, _load_buoys, lat, lon)
        tide_future = executor.submit(copy_context().run, _load_tide, lat, lon, now.strftime("%Y%m%d"))
        # 저녁에도 다음 만조·간조를 보여주려고 내일 조위도 함께 받습니다. (실패해도 오늘 자료는 표시)
        tomorrow_future = executor.submit(copy_context().run, _load_tide, lat, lon, (now + timedelta(days=1)).strftime("%Y%m%d"))

        forecast = []
        try:
            region, region_name, forecast = forecast_future.result()
        except (requests.RequestException, RuntimeError, KeyError):
            errors["forecast"] = "단기 해상예보를 가져오지 못했습니다. 예보 구역을 확인해 주세요."

        buoy = []
        try:
            buoy = buoy_future.result()
        except requests.RequestException:
            errors["buoy"] = "해양 관측 자료를 가져오지 못했습니다."

        tide = None
        try:
            tide = tide_future.result()
        except ValueError:
            errors["tide"] = "조위 API 응답 형식을 읽지 못했습니다."
        except requests.RequestException:
            errors["tide"] = "조위 예측을 가져오지 못했습니다."

        tide_tomorrow = None
        try:
            tide_tomorrow = tomorrow_future.result()
        except (ValueError, requests.RequestException):
            pass

    predictions = normalize_predictions(tide, tide_tomorrow)

    return {
        "area_id": area_id,
        "place": place[:60],
        "region": region,
        "region_name": region_name,
        "coordinates": {"lat": lat, "lon": lon},
        "updated_at": now.isoformat(timespec="minutes"),
        "sources": {
            "forecast": {"name": "기상청 단기 해상예보", "records": forecast},
            "buoy": {"name": "기상청 해양기상부이·파고부이 관측", "records": buoy},
            "tide": {
                "name": "국립해양조사원 TideBED 예측 조위",
                "data": tide,  # 오늘 원자료 (예전 화면 호환용)
                "station": next((point["station"] for point in predictions if point["station"]), ""),
                "predictions": predictions,  # 오늘·내일 1시간 간격 [{time, height_cm, station}]
                "extremes": find_extremes(predictions),  # 만조·간조 추정 [{type: high|low, time, height_cm}]
            },
        },
        "errors": errors,
        "notice": "기관 원자료를 표시합니다. 출조 안전 판단은 최신 특보와 현장 상황도 함께 확인하세요.",
    }
