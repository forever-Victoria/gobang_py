"""玩家匹配队列. 单独一个后台线程, FIFO 取出两人配对."""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, Optional, Set


class Matcher:
    """简单 FIFO 匹配器: 任意两个用户配成一对."""

    def __init__(self, on_match: Callable[[str, str], None]) -> None:
        """on_match(user_a, user_b) 在匹配成功时由后台线程回调."""
        self._on_match = on_match
        self._queue: Deque[str] = deque()
        self._in_queue: Set[str] = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="matcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    # ------------------------- 队列操作 -------------------------
    def add(self, username: str) -> bool:
        """加入匹配队列. 返回 True=新加入, False=已在队列中."""
        with self._cond:
            if username in self._in_queue:
                return False
            self._queue.append(username)
            self._in_queue.add(username)
            self._cond.notify_all()
            return True

    def remove(self, username: str) -> bool:
        with self._cond:
            if username not in self._in_queue:
                return False
            self._in_queue.discard(username)
            try:
                self._queue.remove(username)
            except ValueError:
                pass
            return True

    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def snapshot(self) -> list[str]:
        """返回当前匹配队列快照, 供服务端监控面板只读展示。"""
        with self._lock:
            return list(self._queue)

    # ------------------------- 后台线程 -------------------------
    def _loop(self) -> None:
        while True:
            pair = None
            with self._cond:
                while not self._stop and len(self._queue) < 2:
                    self._cond.wait()
                if self._stop:
                    return
                a = self._queue.popleft()
                b = self._queue.popleft()
                self._in_queue.discard(a)
                self._in_queue.discard(b)
                pair = (a, b)
            try:
                self._on_match(pair[0], pair[1])
            except Exception:  # pragma: no cover - 防御回调异常
                import logging
                logging.getLogger("matcher").exception("on_match callback failed")
