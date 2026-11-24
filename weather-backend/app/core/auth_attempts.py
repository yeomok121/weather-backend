"""Shared, bounded credential throttling for the API worker."""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import monotonic
import math
import unicodedata

from fastapi import HTTPException, Request

WINDOW = 900
MAX_BUCKETS = 16384


@dataclass
class Attempts:
    failures: int = 0
    active: int = 0
    expires: float = 0
    blocked_until: float = 0


_lock = Lock()
_buckets = {}
_next_cleanup = 0


def _reject(seconds):
    delay = max(1, math.ceil(seconds))
    raise HTTPException(
        status_code=429,
        detail=f"인증 시도가 많습니다. {delay}초 후 다시 시도해 주세요.",
        headers={"Retry-After": str(delay)},
    )


@contextmanager
def credential_attempt(request: Request, username: str, *, scope="credentials", count_request=False):
    global _next_cleanup
    identity = unicodedata.normalize("NFKD", username.strip().casefold())
    identity = "".join(char for char in identity if not unicodedata.combining(char))
    account = sha256(identity.encode(errors="replace")).digest()
    # Only use the peer address resolved by the server's trusted proxy setup.
    peer = request.client.host if request.client else "unknown"
    limits = [((scope, "account", account), 5, 2), ((scope, "ip", peer), 30, 4)]

    with _lock:
        now = monotonic()
        if now >= _next_cleanup:
            for key, bucket in tuple(_buckets.items()):
                if not bucket.active and max(bucket.expires, bucket.blocked_until) <= now:
                    del _buckets[key]
            _next_cleanup = now + 30
        if len(_buckets) + sum(key not in _buckets for key, _, _ in limits) > MAX_BUCKETS:
            _reject(30)

        buckets = []
        for key, threshold, max_active in limits:
            bucket = _buckets.setdefault(key, Attempts())
            if not bucket.active and max(bucket.expires, bucket.blocked_until) <= now:
                bucket.failures = 0
                bucket.blocked_until = 0
            if bucket.blocked_until > now:
                _reject(bucket.blocked_until - now)
            if bucket.active >= min(max_active, max(1, threshold - bucket.failures)):
                _reject(1)
            buckets.append((bucket, threshold))
        for bucket, _ in buckets:
            bucket.active += 1

    failed = count_request
    try:
        yield
    except HTTPException as error:
        failed = count_request or error.status_code == 401
        raise
    finally:
        with _lock:
            now = monotonic()
            for bucket, threshold in buckets:
                bucket.active -= 1
                if failed:
                    bucket.failures += 1
                    bucket.expires = now + WINDOW
                    if bucket.failures >= threshold:
                        exponent = min(bucket.failures - threshold, 5)
                        bucket.blocked_until = now + min(WINDOW, 30 * 2 ** exponent)
