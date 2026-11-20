"""낚시 페이지: 기관 API 응답을 잠시 보관하는 메모리 캐시.

같은 자료를 짧은 시간 안에 다시 요청하면 기관 API를 부르지 않고 보관한 값을 돌려줍니다.
(기관 API 호출 한도 절약 + 응답 속도 개선) 실패한 결과는 보관하지 않습니다.

같은 키를 동시에 여러 요청이 찾으면 기관 API는 한 번만 부르고, 나머지는 그 결과를 기다려 함께 씁니다.
(예: 보관 시간이 끝난 직후 포인트 목록·바다 정보·즐겨찾기 비교가 한꺼번에 바다낚시지수 전체를 내려받던 문제)
"""
import threading
import time


class _Flight:
    """지금 불러오는 중인 키 하나: 끝나면 done이 켜지고 value 또는 error가 채워집니다."""
    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error = None


class TTLCache:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._items = {}
        self._flights = {}
        self._lock = threading.Lock()

    def get_or_load(self, key, ttl_seconds: float, loader):
        """보관 중인 값이 있으면 (값, True), 없으면 loader()를 불러 저장하고 (값, False)를 돌려줍니다.
        다른 요청이 같은 키를 불러오는 중이면 기다렸다가 그 결과(실패면 같은 오류)를 (값, True)로 돌려줍니다."""
        with self._lock:
            item = self._items.get(key)
            if item and item[0] > self._clock():
                return item[1], True
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value, True
        try:
            value = loader()  # 예외가 나면 저장하지 않고 그대로 올려 보냅니다.
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            flight.value = value
            with self._lock:
                self._items[key] = (self._clock() + ttl_seconds, value)
                if len(self._items) > 256:
                    expired = [k for k, (expires_at, _) in self._items.items() if expires_at <= self._clock()]
                    for k in expired:
                        self._items.pop(k, None)
            return value, False
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()

    def clear(self):
        with self._lock:
            self._items.clear()
