"""ASOS station winds, joined to the official station inventory by station ID."""
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from app.fishing.cache import TTLCache
from app.fishing.providers import kma_key

KST = timezone(timedelta(hours=9))
BASE = "https://apihub.kma.go.kr/api/typ01/url"
_cache = TTLCache()
_lock = threading.Lock()
_retry_until = 0
_failure = None


class WindError(Exception):
    def __init__(self, message, code="upstream_error"):
        super().__init__(message)
        self.code = code


def _fetch(endpoint, **params):
    key = kma_key()
    if not key:
        raise WindError("바람 관측 연결 설정을 확인하고 있습니다.", "not_configured")
    try:
        response = requests.get(f"{BASE}/{endpoint}", params={**params, "authKey": key}, timeout=(5, 15))
        if response.status_code in (401, 403):
            stage = "관측소 위치(SFC)" if endpoint == "stn_inf.php" else "풍향·풍속 시간자료"
            raise WindError(f"기상청 {stage} API의 활용 승인·인증 설정 확인이 필요합니다.", "provider_forbidden")
        if response.status_code == 429:
            raise WindError("기상청 바람 관측 조회 한도에 도달했습니다. 잠시 후 다시 확인해 주세요.", "rate_limited")
        response.raise_for_status()
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError:
            return response.content.decode("euc-kr", errors="replace")
    except requests.RequestException:
        raise WindError("기상청 바람 관측에 연결하지 못했습니다. 잠시 후 다시 확인해 주세요.") from None


def parse_stations(text):
    required = {"STN_ID", "LON", "LAT", "STN_KO"}
    columns = None
    stations = {}
    for line in text.splitlines():
        fields = line.lstrip("# ").split()
        # The live SFC header splits STN_ID across two comment lines (STN / ID).
        # Only normalize the first column; a later STN column is the managing office.
        if fields and fields[0] == "STN" and {"LON", "LAT", "STN_KO"}.issubset(fields):
            fields[0] = "STN_ID"
        if line.startswith("#") and required.issubset(fields):
            columns = {name: fields.index(name) for name in required}
            continue
        if not columns or not re.match(r"^\s*\d", line):
            continue
        try:
            identifier = int(fields[columns["STN_ID"]])
            lon, lat = float(fields[columns["LON"]]), float(fields[columns["LAT"]])
            name = fields[columns["STN_KO"]]
        except (ValueError, IndexError):
            continue
        if math.isfinite(lat) and math.isfinite(lon) and 30 <= lat <= 44 and 118 <= lon <= 134:
            stations[identifier] = {"id": identifier, "name": name, "lat": lat, "lon": lon}
    if not stations:
        raise WindError("기상청 관측소 위치 자료를 확인하지 못했습니다.", "invalid_format")
    return stations


def parse_winds(text, stations, now):
    records = {}
    for line in text.splitlines():
        if not re.match(r"^\s*\d{12}\s", line):
            continue
        fields = line.split()
        try:
            observed = datetime.strptime(fields[0], "%Y%m%d%H%M").replace(tzinfo=KST)
            identifier = int(fields[1])
            direction, speed, gust = float(fields[2]), float(fields[3]), float(fields[5])
        except (ValueError, IndexError):
            continue
        if identifier not in stations or not math.isfinite(speed) or not 0 <= speed <= 150:
            continue
        if not now - timedelta(hours=3) <= observed <= now + timedelta(minutes=5):
            continue
        if identifier in records and records[identifier]["observed_at"] >= observed.isoformat():
            continue
        records[identifier] = {
            **stations[identifier], "speed": speed,
            # ASOS uses 36 compass sectors. 0 is calm/undefined, 36 is north.
            "direction": (direction * 10) % 360 if math.isfinite(direction) and 1 <= direction <= 36 else None,
            "gust": gust if math.isfinite(gust) and 0 <= gust <= 200 else None,
            "observed_at": observed.isoformat(),
        }
    if not records:
        raise WindError("최근 3시간 이내의 유효한 바람 관측 자료가 없습니다.", "no_data")
    return sorted(records.values(), key=lambda item: item["id"])


def _load_summary():
    now = datetime.now(KST)
    stations, _ = _cache.get_or_load("stations", 86400, lambda: parse_stations(
        _fetch("stn_inf.php", inf="SFC", stn=0, help=1)))
    records = parse_winds(_fetch("kma_sfctm2.php", stn=0, help=1), stations, now)
    return {"source": "기상청 ASOS 시간 관측", "generated_at": now.isoformat(),
            "latest_at": max(record["observed_at"] for record in records), "stations": records}


def summary():
    global _retry_until, _failure
    with _lock:
        if time.monotonic() < _retry_until:
            raise WindError(*_failure)
        try:
            data, _ = _cache.get_or_load("winds", 300, _load_summary)
            return data
        except WindError as error:
            _failure = (str(error), error.code)
            _retry_until = time.monotonic() + 30
            raise
