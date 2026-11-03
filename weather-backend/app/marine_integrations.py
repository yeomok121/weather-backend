"""Secret-safe connectivity checks for marine data providers."""

import os
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import requests


KHOA_BASE = "https://apis.data.go.kr/1192136"
KMA_BASE = "https://apihub.kma.go.kr/api/typ01/url"
TIMEOUT_SECONDS = 12


def _safe_result(name: str, configured: bool, success: bool = False,
                 status_code: int | None = None, detail: str = "") -> dict:
    return {
        "name": name,
        "configured": configured,
        "success": success,
        "http_status": status_code,
        "detail": detail,
    }


def _khoa_check(name: str, env_name: str, path: str, params: dict) -> dict:
    key = os.getenv(env_name, "").strip()
    if not key:
        return _safe_result(name, False, detail=f"{env_name} 환경변수가 없습니다")

    request_params = {**params, "serviceKey": key}
    try:
        response = requests.get(f"{KHOA_BASE}/{path}", params=request_params,
                               timeout=TIMEOUT_SECONDS)
        body = response.text[:200_000]
        if response.status_code >= 400:
            return _safe_result(name, True, status_code=response.status_code,
                                detail="기관 서버가 HTTP 오류를 반환했습니다")

        result_code = None
        try:
            payload = response.json()
            header = payload.get("response", {}).get("header", {})
            if not header and isinstance(payload, dict):
                header = payload.get("header", {})
            result_code = str(header.get("resultCode", "")) or None
        except (ValueError, AttributeError):
            try:
                root = ElementTree.fromstring(body)
                result_code = root.findtext(".//resultCode")
            except ElementTree.ParseError:
                pass

        if result_code is not None and result_code not in {"00", "0"}:
            # Never return raw provider messages; they may echo request details.
            return _safe_result(name, True, status_code=response.status_code,
                                detail=f"기관 응답 코드 {result_code}: 인증·승인·요청 조건을 확인하세요")
        if not body.strip():
            return _safe_result(name, True, status_code=response.status_code,
                                detail="기관 응답이 비어 있습니다")
        if re.search(r"SERVICE_KEY_IS_NULL|SERVICE_KEY_IS_NOT_REGISTERED|SERVICE_ACCESS_DENIED|PERMISSION_DENIED", body, re.I):
            return _safe_result(name, True, status_code=response.status_code,
                                detail="인증키 또는 API 활용 승인 상태를 확인하세요")
        return _safe_result(name, True, True, response.status_code,
                            "인증 및 API 응답을 확인했습니다")
    except requests.RequestException:
        return _safe_result(name, True, detail="기관 API 연결에 실패했습니다")


def _kma_check(name: str, url: str, params: dict) -> dict:
    key = os.getenv("KMA_API_HUB_KEY", "").strip()
    if not key:
        return _safe_result(name, False, detail="KMA_API_HUB_KEY 환경변수가 없습니다")

    try:
        response = requests.get(url, params={**params, "authKey": key},
                                timeout=TIMEOUT_SECONDS)
        body = response.text[:200_000]
        if response.status_code >= 400:
            return _safe_result(name, True, status_code=response.status_code,
                                detail="기상청 API허브가 HTTP 오류를 반환했습니다")
        if not body.strip():
            return _safe_result(name, True, status_code=response.status_code,
                                detail="기상청 API허브 응답이 비어 있습니다")
        if re.search(r"SERVICE_KEY|SERVICE_ACCESS_DENIED|PERMISSION_DENIED|invalid.{0,20}(key|auth)|auth(key|entication).{0,20}(error|fail)|unauthori[sz]ed|인증키.{0,20}(오류|없|확인|유효하지|만료)|허용되지 않", body, re.I):
            return _safe_result(name, True, status_code=response.status_code,
                                detail="인증키 또는 API 활용 신청 상태를 확인하세요")
        return _safe_result(name, True, True, response.status_code,
                            "인증 및 API 응답을 확인했습니다")
    except requests.RequestException:
        return _safe_result(name, True, detail="기상청 API허브 연결에 실패했습니다")


def check_marine_integrations() -> dict:
    """Call each configured provider once; never return keys or raw responses."""
    now = datetime.now(timezone(timedelta(hours=9)))
    date = now.strftime("%Y%m%d")
    forecast_start = (now - timedelta(hours=24)).strftime("%Y%m%d%H")
    forecast_end = now.strftime("%Y%m%d%H")

    checks = [
        _khoa_check(
            "바다낚시지수", "KHOA_FISHING_API_KEY",
            "fcstFishingv2/GetFcstFishingApiServicev2",
            {"gubun": "갯바위", "placeName": "김녕", "reqDate": date,
             "numOfRows": "1", "pageNo": "1", "type": "json"},
        ),
        _khoa_check(
            "TideBED 예측 조위", "KHOA_TIDEBED_API_KEY",
            "tidebed/GetTidebedApiService",
            {"reqDate": date, "lot": "126.44527", "lat": "33.96194",
             "min": "60", "type": "json"},
        ),
        _kma_check(
            "단기 해상예보",
            f"{KMA_BASE}/fct_afs_do.php",
            {"reg": "12A20100", "tmfc1": forecast_start,
             "tmfc2": forecast_end, "disp": "1", "help": "1"},
        ),
        _kma_check(
            "기상특보",
            f"{KMA_BASE}/wrn_now_data.php",
            {"fe": "f", "tm": "", "disp": "1", "help": "1"},
        ),
        _kma_check(
            "해양기상부이·파고부이 관측",
            f"{KMA_BASE}/sea_obs.php",
            {"tm": "", "stn": "0", "help": "1"},
        ),
    ]
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "success_count": sum(1 for check in checks if check["success"]),
        "total_count": len(checks),
        "checks": checks,
    }
