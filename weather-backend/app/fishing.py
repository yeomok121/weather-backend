"""Public, key-safe aggregation for the fishing weather dashboard."""

import os
from datetime import datetime, timedelta, timezone

import requests

KHOA_BASE = "https://apis.data.go.kr/1192136"
KMA_BASE = "https://apihub.kma.go.kr/api/typ01/url"
SEOUL = timezone(timedelta(hours=9))


def _fetch(url: str, params: dict, timeout: int = 15) -> requests.Response:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response


def _safe_json(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        return None


def _kma_records(response: requests.Response):
    """Keep provider text out of the UI except for parsed data rows."""
    headers = []
    rows = []
    for raw in response.text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#"):
            candidate = line.lstrip("# ").strip()
            if "," in candidate and len(candidate.split(",")) >= 4:
                headers = [part.strip() for part in candidate.split(",")]
            continue
        cols = [part.strip() for part in line.split(",")]
        if len(cols) >= 4:
            if headers and len(headers) == len(cols):
                rows.append(dict(zip(headers, cols)))
            else:
                rows.append({"fields": cols})
    return rows


def get_fishing_summary(place: str, region: str) -> dict:
    now = datetime.now(SEOUL)
    date = now.strftime("%Y%m%d")
    forecast_key = os.getenv("KMA_API_HUB_KEY", "").strip()
    tide_key = os.getenv("KHOA_TIDEBED_API_KEY", "").strip()
    if not forecast_key or not tide_key:
        raise RuntimeError("해양 예보 또는 조위 API 설정이 아직 완료되지 않았습니다.")

    start = (now - timedelta(hours=24)).strftime("%Y%m%d%H")
    end = now.strftime("%Y%m%d%H")
    errors = {}
    forecast = []
    buoy = []
    tide = None

    try:
        response = _fetch(
            f"{KMA_BASE}/fct_afs_do.php",
            {"reg": region, "tmfc1": start, "tmfc2": end, "disp": "1", "help": "1", "authKey": forecast_key},
        )
        forecast = _kma_records(response)
    except requests.RequestException:
        errors["forecast"] = "단기 해상예보를 가져오지 못했습니다. 예보 구역을 확인해 주세요."

    try:
        response = _fetch(
            f"{KMA_BASE}/sea_obs.php",
            {"tm": "", "stn": "0", "help": "1", "authKey": forecast_key},
        )
        buoy = _kma_records(response)
    except requests.RequestException:
        errors["buoy"] = "해양 관측 자료를 가져오지 못했습니다."

    try:
        response = _fetch(
            f"{KHOA_BASE}/tidebed/GetTidebedApiService",
            {"reqDate": date, "lot": "126.44527", "lat": "33.96194", "min": "60", "type": "json", "serviceKey": tide_key},
        )
        tide = _safe_json(response)
        if tide is None:
            errors["tide"] = "조위 API 응답 형식을 읽지 못했습니다."
    except requests.RequestException:
        errors["tide"] = "조위 예측을 가져오지 못했습니다."

    return {
        "place": place[:60],
        "region": region,
        "updated_at": now.isoformat(timespec="minutes"),
        "sources": {
            "forecast": {"name": "기상청 단기 해상예보", "records": forecast},
            "buoy": {"name": "기상청 해양기상부이·파고부이 관측", "records": buoy},
            "tide": {"name": "국립해양조사원 TideBED 예측 조위", "data": tide},
        },
        "errors": errors,
        "notice": "기관 원자료를 표시합니다. 출조 안전 판단은 최신 특보와 현장 상황도 함께 확인하세요.",
    }
