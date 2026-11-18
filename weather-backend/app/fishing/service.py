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
from app.fishing.areas import FISHING_AREAS, REGION_NAMES, WARNING_REGIONS
from app.fishing.warnings import parse_warnings, warnings_for_regions
from app.fishing.zones import zone_for_point
from app.fishing.cache import TTLCache
from app.fishing import fishing_index
from app.fishing.tides import find_extremes, normalize_predictions

# 자료별 보관 시간(초). 기관 갱신 주기보다 짧게 잡았습니다.
FORECAST_TTL_SECONDS = 10 * 60
BUOY_TTL_SECONDS = 5 * 60
TIDE_TTL_SECONDS = 30 * 60
FISHING_INDEX_TTL_SECONDS = 60 * 60  # 바다낚시지수는 하루 몇 차례만 갱신됩니다.
FISHING_INDEX_GUBUN = "갯바위"
WARNING_TTL_SECONDS = 5 * 60  # 특보는 수시로 바뀌므로 짧게 보관합니다.

cache = TTLCache()


def summary_for_point(point_id: str) -> dict | None:
    """포인트 이름으로 요약: 위치는 포인트 좌표, 해상예보·특보는 연결된 바다 구역."""
    point = get_point(point_id)
    if point is None:
        return None
    zone = zone_for_point(point["zone_from"]) if point.get("zone_from") else None
    return get_fishing_summary(
        place=point["name"], region=zone["forecast_reg"] if zone else None, lat=point["lat"], lon=point["lon"],
        warning_regions=zone["warning_regions"] if zone else [], region_name=zone["forecast_name"] if zone else "",
        point_id=point_id, tide_system=zone["tide_system"] if zone else 8,
    )


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
    def load():
        rows = providers.fetch_marine_forecast(region, now)
        # 세부 구역 예보가 비어 있으면 상위 구역(예: 12B10201 → 12B10000)으로 한 번 더 찾습니다.
        parent = region[:5] + "000"
        if not rows and len(region) == 8 and parent != region:
            rows = providers.fetch_marine_forecast(parent, now)
        return rows
    rows, _ = cache.get_or_load(("forecast", region), FORECAST_TTL_SECONDS, load)
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


def _fetch_tide_cached(lat: float, lon: float, date: str):
    def load():
        data = providers.fetch_tide_prediction(lat, lon, date)
        if data is None:
            raise ValueError("unreadable tide response")  # 읽지 못한 응답은 캐시하지 않습니다.
        return data
    data, _ = cache.get_or_load(("tide", date, round(lat, 4), round(lon, 4)), TIDE_TTL_SECONDS, load)
    return data


# 포인트가 섬·육지 위라 조위 예측이 거부되면 바다 쪽으로 조금씩(약 5·10·20 km) 옮겨 다시 찾습니다.
TIDE_OFFSETS = [(0.0, 0.0)] + [(step * dlat, step * dlon) for step in (0.05, 0.1, 0.2)
                               for dlat, dlon in ((0, 1), (0, -1), (1, 0), (-1, 0))]


def _load_tide(lat: float, lon: float, date: str):
    """(조위 응답, 실제로 조회한 좌표). 찾은 좌표는 하루 동안 기억해 다음 조회와 내일 자료에 씁니다."""
    def find_coords():
        for dlat, dlon in TIDE_OFFSETS:
            try:
                _fetch_tide_cached(lat + dlat, lon + dlon, date)
                return round(lat + dlat, 5), round(lon + dlon, 5)
            except providers.TideParameterError:
                continue
        raise providers.TideParameterError("no tide prediction near this point")
    (tide_lat, tide_lon), _ = cache.get_or_load(("tide_coords", round(lat, 4), round(lon, 4)), 24 * 3600, find_coords)
    return _fetch_tide_cached(tide_lat, tide_lon, date), (tide_lat, tide_lon)


def _load_fishing_index(lat: float, lon: float):
    return fishing_index.summarize(_load_index_items(), lat, lon)


def _load_index_items():
    items, _ = cache.get_or_load(("fishing_index", FISHING_INDEX_GUBUN), FISHING_INDEX_TTL_SECONDS,
                                 lambda: providers.fetch_fishing_index(FISHING_INDEX_GUBUN))
    return items


def get_fishing_points() -> list[dict]:
    """바다낚시지수 포인트 목록 + 바다 구역 연결 (지도·목록에서 고르기용)."""
    if not providers.fishing_index_key():
        raise RuntimeError("바다낚시지수 API 설정이 아직 완료되지 않았습니다.")
    catalog = fishing_index.list_points(_load_index_items(), 0, 0)
    known = [point for point in catalog if zone_for_point(point["name"])]
    points = []
    for point in catalog:
        zone_from = point["name"] if zone_for_point(point["name"]) else _nearest_zoned_name(point, known)
        zone = zone_for_point(zone_from) if zone_from else None
        points.append({
            "id": point["name"], "name": point["name"], "lat": point["lat"], "lon": point["lon"],
            "sea": zone["sea"] if zone else "", "zone": zone["zone"] if zone else "",
            "region_name": zone["forecast_name"] if zone else "", "tide_system": zone["tide_system"] if zone else 8,
            # 연결표에 없는 포인트(이름이 바뀐 경우 등)는 가까운 포인트의 구역을 이어받습니다.
            "zone_from": zone_from or "",
        })
    sea_order = {"서해": 0, "남해": 1, "제주": 2, "동해": 3}
    return sorted(points, key=lambda item: (sea_order.get(item["sea"], 9), item["name"]))


NEAREST_ZONE_KM = 30


def _nearest_zoned_name(point: dict, known: list[dict]) -> str | None:
    best = min(known, key=lambda other: providers.distance_km(point["lat"], point["lon"], other["lat"], other["lon"]), default=None)
    if best is None or providers.distance_km(point["lat"], point["lon"], best["lat"], best["lon"]) > NEAREST_ZONE_KM:
        return None
    return best["name"]


def get_point(point_id: str) -> dict | None:
    return next((point for point in get_fishing_points() if point["id"] == point_id), None)


def _load_warnings():
    rows, _ = cache.get_or_load(("warnings",), WARNING_TTL_SECONDS, lambda: parse_warnings(providers.fetch_warnings_text()))
    return rows


def get_fishing_summary(place: str, region: str | None, lat: float = 33.96194, lon: float = 126.44527, area_id: str = "",
                        warning_regions: list[str] | None = None, region_name: str = "", point_id: str = "", tide_system: int | None = None) -> dict:
    """region=None이면 해상예보 구역이 설정되지 않은 포인트라 해상예보를 건너뜁니다."""
    now = datetime.now(providers.SEOUL)
    if not providers.kma_key() or not providers.tidebed_key():
        raise RuntimeError("해양 예보 또는 조위 API 설정이 아직 완료되지 않았습니다.")

    errors = {}
    region_name = region_name or REGION_NAMES.get(region or "", "")
    if warning_regions is None:
        warning_regions = WARNING_REGIONS.get(region or "", [])

    def forecast_task():
        if region is None:
            raise LookupError("no forecast zone")
        if region_name:
            return region, region_name, _load_forecast(region, now)
        resolved_region, resolved_name = _resolve_region(region)
        return resolved_region, resolved_name, _load_forecast(resolved_region, now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        forecast_future = executor.submit(copy_context().run, forecast_task)
        buoy_future = executor.submit(copy_context().run, _load_buoys, lat, lon)
        tide_future = executor.submit(copy_context().run, _load_tide, lat, lon, now.strftime("%Y%m%d"))
        # 저녁에도 다음 만조·간조를 보여주려고 내일 조위도 함께 받습니다. (실패해도 오늘 자료는 표시)
        tomorrow_future = executor.submit(copy_context().run, _load_tide, lat, lon, (now + timedelta(days=1)).strftime("%Y%m%d"))
        # 바다낚시지수는 키가 없어도 나머지 자료는 보여줍니다.
        index_future = executor.submit(copy_context().run, _load_fishing_index, lat, lon) if providers.fishing_index_key() else None
        warnings_future = executor.submit(copy_context().run, _load_warnings)

        forecast = []
        try:
            region, region_name, forecast = forecast_future.result()
        except LookupError:
            errors["forecast"] = "이 포인트는 해상예보 구역이 아직 설정되지 않았습니다."
        except (requests.RequestException, RuntimeError, KeyError):
            errors["forecast"] = "단기 해상예보를 가져오지 못했습니다. 예보 구역을 확인해 주세요."

        buoy = []
        try:
            buoy = buoy_future.result()
        except requests.RequestException:
            errors["buoy"] = "해양 관측 자료를 가져오지 못했습니다."

        tide, tide_coords = None, None
        try:
            tide, tide_coords = tide_future.result()
        except providers.TideParameterError:
            errors["tide"] = "이 위치 근처에서 조위 예측을 찾지 못했습니다."
        except ValueError:
            errors["tide"] = "조위 API 응답 형식을 읽지 못했습니다."
        except requests.RequestException:
            errors["tide"] = "조위 예측을 가져오지 못했습니다."

        tide_tomorrow = None
        try:
            tide_tomorrow, _ = tomorrow_future.result()
        except (ValueError, requests.RequestException):
            pass

        index = {"point": None, "nearby": [], "days": []}
        if index_future is None:
            errors["fishing_index"] = "바다낚시지수 API 설정이 아직 완료되지 않았습니다."
        else:
            try:
                index = index_future.result()
            except (ValueError, requests.RequestException):
                errors["fishing_index"] = "바다낚시지수를 가져오지 못했습니다."

        warning_rows = None
        if not warning_regions:
            warnings_future.cancel()
            errors["warnings"] = "이 포인트는 기상특보 구역이 아직 설정되지 않았습니다. 기상청 특보를 직접 확인해 주세요."
        else:
            try:
                warning_rows = warnings_future.result()
            except (ValueError, requests.RequestException):
                errors["warnings"] = "기상특보를 가져오지 못했습니다. 출조 전 기상청 특보를 직접 확인해 주세요."

    predictions = normalize_predictions(tide, tide_tomorrow)

    return {
        "area_id": area_id,
        "point_id": point_id,
        "tide_system": tide_system,
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
                # 포인트가 섬 위라 바다 쪽으로 옮겨 조회했으면 그 좌표와 거리 (화면 안내용)
                "location": None if tide_coords is None else {
                    "lat": tide_coords[0], "lon": tide_coords[1],
                    "shifted_km": providers.distance_km(lat, lon, tide_coords[0], tide_coords[1]),
                },
                "station": next((point["station"] for point in predictions if point["station"]), ""),
                "predictions": predictions,  # 오늘·내일 1시간 간격 [{time, height_cm, station}]
                "extremes": find_extremes(predictions),  # 만조·간조 추정 [{type: high|low, time, height_cm}]
            },
            "fishing_index": {"name": "국립해양조사원 바다낚시지수", "gubun": FISHING_INDEX_GUBUN, **index},
            "warnings": {
                "name": "기상청 특보현황",
                "regions": warning_regions,
                # 자료를 못 받았으면 None (화면에서 '특보 없음'으로 잘못 보이지 않게)
                "items": None if warning_rows is None else warnings_for_regions(warning_rows, warning_regions),
            },
        },
        "errors": errors,
        "notice": "기관 원자료를 표시합니다. 출조 안전 판단은 최신 특보와 현장 상황도 함께 확인하세요.",
    }
