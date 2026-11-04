"""Weather diagnostics: never log credentials, raw URLs, bodies or exception messages."""
import contextvars
import json
import logging
import os
import sys
import time
import traceback
import uuid
from urllib.parse import urlsplit

import requests

request_id_var = contextvars.ContextVar("weather_request_id", default="-")
logger = logging.getLogger("weather.diagnostics")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def emit(event, level=logging.INFO, **fields):
    logger.log(level, json.dumps({"event": event, "request_id": request_id_var.get(), **fields}, ensure_ascii=False))


def log_error(event, exc, **fields):
    # Standard exception strings/tracebacks may contain API keys in a request URL.
    # Record exception types and code locations, not their messages or locals.
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = [{"file": os.path.basename(f.filename), "line": f.lineno, "function": f.name}
              for f in traceback.extract_tb(exc.__traceback__)[-12:]]
    emit(event, level=logging.ERROR, error_type=type(exc).__name__, error_chain=chain, frames=frames, **fields)


def _endpoint(url):
    parts = urlsplit(str(url))
    return (parts.hostname or "") + parts.path


def logged_get(url, **kwargs):
    endpoint = _endpoint(url)
    call_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    params = kwargs.get("params") or {}
    if isinstance(params, dict):
        for key in ("serviceKey", "appid"):
            if key in params and not params[key]:
                emit("external_key_missing", level=logging.WARNING, endpoint=endpoint, key_name=key, call_id=call_id)
    emit("external_start", endpoint=endpoint, call_id=call_id, timeout_seconds=kwargs.get("timeout"))
    try:
        response = requests.get(url, **kwargs)
    except Exception as exc:
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            kind = "connect_timeout"
        elif isinstance(exc, requests.exceptions.ReadTimeout):
            kind = "read_timeout"
        elif isinstance(exc, requests.exceptions.Timeout):
            kind = "timeout"
        elif isinstance(exc, requests.exceptions.SSLError):
            kind = "tls_error"
        elif isinstance(exc, requests.exceptions.ConnectionError):
            kind = "connection_error"
        else:
            kind = "unexpected_error"
        log_error("external_failed", exc, endpoint=endpoint, call_id=call_id, kind=kind,
                  elapsed_seconds=round(time.perf_counter() - started, 3))
        raise
    # Preserve the original status handling: do not add raise_for_status().
    response._weather_call_id = call_id
    emit("external_end", level=logging.INFO if 200 <= response.status_code < 300 else logging.WARNING,
         endpoint=endpoint, call_id=call_id, status=response.status_code,
         elapsed_seconds=round(time.perf_counter() - started, 3))
    return response


def logged_json(response):
    endpoint = _endpoint(response.url)
    call_id = getattr(response, "_weather_call_id", "-")
    try:
        data = response.json()
    except Exception as exc:
        log_error("external_json_failed", exc, endpoint=endpoint, call_id=call_id, status=response.status_code)
        raise
    # KMA may return HTTP 200 with an application-level error.
    if isinstance(data, dict):
        root = data.get("response")
        header = root.get("header") if isinstance(root, dict) else None
        if isinstance(header, dict):
            code = header.get("resultCode")
            code_text = str(code)
            if code is not None and code_text not in ("00", "0", "NORMAL_SERVICE"):
                safe_code = code_text if code_text.isdigit() and len(code_text) <= 8 else "non_numeric_code"
                emit("external_business_error", level=logging.WARNING, endpoint=endpoint,
                     call_id=call_id, result_code=safe_code)
    return data


def run_weather_task(name, fn, *args):
    started = time.perf_counter()
    emit("task_start", task=name)
    try:
        result = fn(*args)
    except Exception as exc:
        log_error("task_failed", exc, task=name, elapsed_seconds=round(time.perf_counter() - started, 3))
        raise
    degraded = result is None or result == [] or (isinstance(result, dict) and result.get("level") == "점검중")
    emit("task_end", level=logging.WARNING if degraded else logging.INFO, task=name,
         empty_or_degraded=degraded, elapsed_seconds=round(time.perf_counter() - started, 3))
    return result


def log_environment_status():
    # Values and lengths are deliberately not logged. This checks presence, not validity.
    for name in ("KMA_API_KEY", "OPENWEATHER_API_KEY"):
        emit("config_status", key_name=name, configured=bool(os.getenv(name)))


class RequestLogMiddleware:
    """ASGI middleware; logs arrival before downstream processing starts."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = None
        # No query strings, bodies, authorization headers or client addresses.
        emit("request_start", method=scope.get("method"), path=scope.get("path"))

        async def logged_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", rid.encode("ascii")))
                message["headers"] = headers
                emit("response_start", status=status, elapsed_seconds=round(time.perf_counter() - started, 3))
            await send(message)

        try:
            await self.app(scope, receive, logged_send)
        except Exception as exc:
            log_error("request_failed", exc, status=status, elapsed_seconds=round(time.perf_counter() - started, 3))
            raise
        finally:
            emit("request_end", status=status, elapsed_seconds=round(time.perf_counter() - started, 3))
            request_id_var.reset(token)
