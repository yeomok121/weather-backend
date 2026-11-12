"""KMA HSR observations reprojected with the provider's own grid coordinates.

Only rendered PNGs and sampled pixel lookups are retained in memory. All public
requests share a bounded cache and one provider download at a time per worker.
"""
import io
import os
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from PIL import Image

KST = timezone(timedelta(hours=9))
BASE = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url"
BOUNDS = [[30, 118], [44, 134]]
SIZE = 768
STEP = 2
MAX_BYTES = 32_000_000
LEGEND = [
    {"value": 0, "color": "#55b9f3"},
    {"value": 10, "color": "#21c7cb"},
    {"value": 20, "color": "#37bf64"},
    {"value": 30, "color": "#eacb32"},
    {"value": 40, "color": "#f48b32"},
    {"value": 50, "color": "#d94357"},
]
_lock = threading.Lock()
_frames = OrderedDict()
_failures = OrderedDict()
_lookup = None
_manifest = None
_manifest_until = 0
_retry_after = 0
_last_error = None


class RadarError(Exception):
    def __init__(self, message, status=502, code="upstream_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def _fetch(path, **params):
    key = os.getenv("KMA_API_HUB_KEY", "").strip()
    if not key:
        raise RadarError("레이더 연결 설정을 확인하고 있습니다. 잠시 후 다시 시도해 주세요.", 503, "not_configured")
    try:
        with requests.get(f"{BASE}/{path}", params={**params, "authKey": key},
                          timeout=(5, 25), stream=True) as response:
            if response.status_code in (401, 403):
                stage = "강수 관측" if path == "nph-rdr_cmp1_api" else "지도 좌표"
                raise RadarError(f"기상청이 {stage} 자료 접근을 거절했습니다. 운영자가 API 활용 승인·인증 설정을 확인하고 있습니다.",
                                 503, "provider_forbidden")
            if response.status_code == 429:
                raise RadarError("기상청 레이더 조회 한도에 도달했습니다. 잠시 후 다시 확인해 주세요.", 503, "rate_limited")
            if response.status_code == 404 and path == "nph-rdr_cmp1_api":
                raise RadarError("해당 시각의 레이더 관측 자료가 아직 없습니다.", 502, "no_data")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise RadarError("레이더 자료 크기를 확인하지 못했습니다.")
            return bytes(content)
    except requests.RequestException:
        # Request exceptions may contain authKey in their URL.
        raise RadarError("기상청 레이더 자료에 연결하지 못했습니다. 다시 시도해 주세요.") from None


def decode_grid(content, kind, expected_shape=None):
    """The CGI binary format is nx,ny (short), then short ECHO or float coords."""
    itemsize = 2 if kind == "echo" else 4
    for endian in ("<", ">"):
        if len(content) < 4:
            break
        nx, ny = struct.unpack(f"{endian}hh", content[:4])
        if not (2 <= nx <= 6000 and 2 <= ny <= 6000):
            continue
        if len(content) != 4 + nx * ny * itemsize:
            continue
        dtype = f"{endian}{'i2' if kind == 'echo' else 'f4'}"
        shape = (ny, nx)
        if expected_shape is not None:
            # The live coordinate CGI reports ny,nx in its header, but its body
            # still runs west-to-east in nx-wide rows, like the ECHO endpoint.
            # Normalize only this exact reversal; do not transpose the payload.
            if shape not in (expected_shape, expected_shape[::-1]):
                continue
            shape = expected_shape
        grid = np.frombuffer(content, dtype=dtype, offset=4).reshape(shape)
        if kind != "echo":
            low, high = (20, 55) if kind == "lat" else (100, 150)
            sample = grid[::100, ::100]
            if not np.all(np.isfinite(sample) & (sample >= low) & (sample <= high)):
                continue
        return grid
    # Known no-data messages can be returned with HTTP 200 instead of a grid.
    message = content[:2000].decode("utf-8", errors="ignore").lower()
    if any(marker in message for marker in ("no data", "not found", "자료가 없습니다", "파일이 없습니다")):
        raise RadarError("해당 시각의 레이더 관측 자료가 아직 없습니다.", 502, "no_data")
    raise RadarError("레이더 자료 형식을 확인하지 못했습니다. 관측 자료를 다시 확인해 주세요.", 502, "invalid_format")


def _mercator(latitude):
    return np.log(np.tan(np.pi / 4 + np.deg2rad(latitude) / 2))


def _pixel_lookup(shape):
    global _lookup
    if _lookup is not None and _lookup[0] == shape:
        return _lookup[1:]
    cols = decode_grid(_fetch("nph-rdr_latlon_api", cmp="HSR", latlon="lon", disp="B"), "lon", shape)
    if cols.shape != shape:
        raise RadarError("레이더 관측 격자와 좌표 격자가 일치하지 않습니다.")
    x = np.floor((cols[::STEP, ::STEP] - 118) / 16 * SIZE).astype(np.int16)
    del cols
    rows = decode_grid(_fetch("nph-rdr_latlon_api", cmp="HSR", latlon="lat", disp="B"), "lat", shape)
    if rows.shape != shape:
        raise RadarError("레이더 관측 격자와 좌표 격자가 일치하지 않습니다.")
    north, south = _mercator(44), _mercator(30)
    y = np.floor((north - _mercator(rows[::STEP, ::STEP])) / (north - south) * SIZE).astype(np.int16)
    del rows
    valid = (x >= 0) & (x < SIZE) & (y >= 0) & (y < SIZE)
    pixels = y[valid].astype(np.int32) * SIZE + x[valid]
    _lookup = (shape, valid, pixels)
    return valid, pixels


def render_grid(grid, valid, pixels):
    # 0 = no coverage/missing, 1 = no echo/below display threshold, 2..7 = dBZ.
    echo = grid[::STEP, ::STEP][valid]
    levels = np.zeros(echo.shape, dtype=np.uint8)
    levels[echo >= -20000] = 1
    levels[echo == -25000] = 1
    observed = echo >= 0
    levels[observed] = np.minimum(echo[observed] // 1000 + 2, 7)
    raster = np.zeros(SIZE * SIZE, dtype=np.uint8)
    np.maximum.at(raster, pixels, levels)
    palette = np.array([
        [110, 122, 140, 48], [0, 0, 0, 0],
        [85, 185, 243, 170], [33, 199, 203, 180], [55, 191, 100, 185],
        [234, 203, 50, 195], [244, 139, 50, 205], [217, 67, 87, 215],
    ], dtype=np.uint8)
    output = io.BytesIO()
    Image.fromarray(palette[raster.reshape(SIZE, SIZE)]).save(output, format="PNG")
    return output.getvalue()


def _frame(stamp):
    if stamp in _frames:
        return _frames[stamp]
    failure = _failures.get(stamp)
    if failure is not None and time.monotonic() < failure[0]:
        raise RadarError(failure[1], failure[2], failure[3])
    try:
        grid = decode_grid(_fetch("nph-rdr_cmp1_api", tm=stamp, cmp="HSR", qcd="MSK",
                                 obs="ECHO", map="HB", disp="B"), "echo")
        valid, pixels = _pixel_lookup(grid.shape)
        image = render_grid(grid, valid, pixels)
    except RadarError as error:
        _failures[stamp] = (time.monotonic() + 30, str(error), error.status, error.code)
        while len(_failures) > 25:
            _failures.popitem(last=False)
        raise
    _failures.pop(stamp, None)
    _frames[stamp] = image
    while len(_frames) > 15:
        _frames.popitem(last=False)
    return image


def manifest():
    global _manifest, _manifest_until, _retry_after, _last_error
    with _lock:
        now = datetime.now(KST)
        if _manifest is not None and time.monotonic() < _manifest_until:
            return _manifest
        if time.monotonic() < _retry_after:
            raise RadarError(*_last_error)
        # Leave ten minutes for provider processing. Never invent a future frame.
        latest = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5 + 10)
        try:
            # A missing publication is retried at most twice at older times.
            # Access denials, quota and format failures are never retried here.
            for attempt in range(3):
                try:
                    _frame(latest.strftime("%Y%m%d%H%M"))
                    break
                except RadarError as error:
                    if error.code != "no_data" or attempt == 2:
                        raise
                    latest -= timedelta(minutes=5)
        except RadarError as error:
            _last_error = (str(error), error.status, error.code)
            _retry_after = time.monotonic() + 30
            raise
        times = [latest - timedelta(minutes=minutes) for minutes in range(60, -1, -10)]
        _manifest = {
            "source": "기상청 HSR 합성 관측", "bounds": BOUNDS, "legend": LEGEND,
            "latest_at": latest.isoformat(), "generated_at": now.isoformat(),
            "frames": [{"id": value.strftime("%Y%m%d%H%M"), "observed_at": value.isoformat(),
                        "path": f"/api/weather/radar/frames/{value:%Y%m%d%H%M}.png"} for value in times],
        }
        _manifest_until = time.monotonic() + 120
        return _manifest


def frame(stamp):
    try:
        observed = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        raise RadarError("올바른 레이더 관측 시각이 필요합니다.", 400, "invalid_time") from None
    now = datetime.now(KST)
    if observed.minute % 5 or not now - timedelta(hours=2) <= observed <= now:
        raise RadarError("최근 2시간 이내의 레이더 관측만 조회할 수 있습니다.", 400, "expired_time")
    with _lock:
        return _frame(stamp)
