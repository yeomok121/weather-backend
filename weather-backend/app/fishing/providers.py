"""낚시 페이지: 기관 API 호출과 응답 해석 (기상청 API허브, 국립해양조사원).

기관 API 하나당 함수 하나입니다. 새 자료(바다낚시지수, 기상특보 등)를 붙일 때는
여기에 fetch_* 함수를 추가하고 service.py에서 불러 쓰면 됩니다.
인증키와 기관 원문 메시지는 화면으로 내보내지 않습니다.
"""

import os
import math
from datetime import datetime, timedelta, timezone

import requests

KHOA_BASE = "https://apis.data.go.kr/1192136"
KMA_BASE = "https://apihub.kma.go.kr/api/typ01/url"
SEOUL = timezone(timedelta(hours=9))
REQUEST_TIMEOUT_SECONDS = 15


def kma_key() -> str:
    return os.getenv("KMA_API_HUB_KEY", "").strip()


def tidebed_key() -> str:
    return os.getenv("KHOA_TIDEBED_API_KEY", "").strip()


def _fetch(url: str, params: dict, timeout: int = REQUEST_TIMEOUT_SECONDS) -> requests.Response:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response


def _safe_json(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        return None


def _kma_records(response: requests.Response, kind: str):
    """Keep provider text out of the UI except for parsed data rows."""
    fallback_headers = {
        "buoy": ["TP", "TM", "STN_ID", "STN_KO", "LON", "LAT", "WH", "WD", "WS", "WS_GST", "TW", "TA", "PA", "HM"],
        "forecast": ["REG_ID", "TM_FC", "TM_EF", "MOD", "NE", "STN", "C", "MAN_ID", "MAN_FC", "W1", "T", "W2", "WS1", "WS2", "WH1", "WH2", "SKY", "PREP", "WF", "ETC"],
    }[kind]
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
            row_headers = headers if len(headers) == len(cols) else fallback_headers[:len(cols)]
            rows.append(dict(zip(row_headers, cols)))
    return rows


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    value = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return round(radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)), 1)


def _region_records(response: requests.Response):
    headers = []
    rows = []
    fallback = ["REG_ID", "TM_ST", "TM_ED", "REG_SP", "REG_NAME"]
    for raw in response.text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#"):
            candidate = line.lstrip("# ").strip()
            if "," in candidate and "REG_ID" in candidate:
                headers = [part.strip() for part in candidate.split(",")]
            continue
        cols = [part.strip() for part in line.split(",")]
        if len(cols) >= 2 and len(cols[0]) == 8 and cols[0].isalnum():
            names = headers if len(headers) == len(cols) else fallback[:len(cols)]
            rows.append(dict(zip(names, cols)))
    return rows


def get_fishing_regions() -> list[dict]:
    """기상청 단기 해상예보 구역 목록."""
    key = kma_key()
    if not key:
        raise RuntimeError("기상청 API 키가 설정되지 않았습니다.")
    response = _fetch(f"{KMA_BASE}/fct_shrt_reg.php", {"tmfc": "0", "help": "1", "authKey": key})
    return _region_records(response)


def fetch_marine_forecast(region: str, now: datetime) -> list[dict]:
    """기상청 단기 해상예보: 최근 48시간 발표분 중 가장 최신 발표의 예보 행."""
    start = (now - timedelta(hours=48)).strftime("%Y%m%d%H")
    end = now.strftime("%Y%m%d%H")
    response = _fetch(
        f"{KMA_BASE}/fct_afs_do.php",
        {"reg": region, "tmfc1": start, "tmfc2": end, "disp": "1", "help": "1", "authKey": kma_key()},
    )
    forecast = _kma_records(response, "forecast")
    if forecast:
        latest_issue = max(row.get("TM_FC", "") for row in forecast)
        forecast = [row for row in forecast if row.get("TM_FC") == latest_issue]
    return forecast


def fetch_buoy_observations() -> list[dict]:
    """기상청 해양기상부이·파고부이 관측: 전국 부이 최신 관측 (거리 계산 전)."""
    response = _fetch(f"{KMA_BASE}/sea_obs.php", {"tm": "", "stn": "0", "help": "1", "authKey": kma_key()})
    buoy = _kma_records(response, "buoy")
    # TP=B is the buoy set used by the fishing dashboard; discard missing sentinel values.
    return [row for row in buoy if row.get("TP") == "B" and row.get("WH") not in (None, "-99", "-99.0")]


def fetch_tide_prediction(lat: float, lon: float, date: str):
    """국립해양조사원 TideBED 예측 조위 (1시간 간격). 응답을 읽지 못하면 None."""
    response = _fetch(
        f"{KHOA_BASE}/tidebed/GetTidebedApiService",
        {"reqDate": date, "lot": str(lon), "lat": str(lat), "min": "60", "pageNo": "1", "numOfRows": "24", "type": "json", "serviceKey": tidebed_key()},
    )
    return _safe_json(response)
