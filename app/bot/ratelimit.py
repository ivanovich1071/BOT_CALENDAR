"""Ограничение частоты AI-запросов на клиента — защита ключа OpenRouter от спама.

Счётчики живут в памяти процесса бота: процесс один, а после перезапуска
начать отсчёт заново не страшно.
"""

import time
from collections import defaultdict, deque

AI_REQUESTS_PER_HOUR = 20


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[int, deque] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


ai_limiter = SlidingWindowLimiter(AI_REQUESTS_PER_HOUR, 3600)
