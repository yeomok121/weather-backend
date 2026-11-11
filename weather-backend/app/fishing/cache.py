"""낚시 페이지: 기관 API 응답을 잠시 보관하는 메모리 캐시.

같은 자료를 짧은 시간 안에 다시 요청하면 기관 API를 부르지 않고 보관한 값을 돌려줍니다.
(기관 API 호출 한도 절약 + 응답 속도 개선) 실패한 결과는 보관하지 않습니다.
"""
import threading
import time


class TTLCache:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._items = {}
        self._lock = threading.Lock()

    def get_or_load(self, key, ttl_seconds: float, loader):
        """보관 중인 값이 있으면 (값, True), 없으면 loader()를 불러 저장하고 (값, False)를 돌려줍니다."""
        now = self._clock()
        with self._lock:
            item = self._items.get(key)
            if item and item[0] > now:
                return item[1], True
        value = loader()  # 예외가 나면 저장하지 않고 그대로 올려 보냅니다.
        with self._lock:
            self._items[key] = (self._clock() + ttl_seconds, value)
            if len(self._items) > 256:
                expired = [k for k, (expires_at, _) in self._items.items() if expires_at <= self._clock()]
                for k in expired:
                    self._items.pop(k, None)
        return value, False

    def clear(self):
        with self._lock:
            self._items.clear()
