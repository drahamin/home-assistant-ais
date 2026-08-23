"""Coalescing background publisher for Home Assistant state updates."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable


class StatePublisher:
    """Publish only the newest value for each entity without blocking CAN reads."""

    def __init__(
        self,
        api_base: str,
        token: str,
        log: Callable[[str], None],
        error_callback: Callable[[str | None], None] | None = None,
        request_timeout: float = 3.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.log = log
        self.error_callback = error_callback
        self.request_timeout = request_timeout
        self._condition = threading.Condition()
        self._pending: dict[str, bytes] = {}
        self._last_sent: dict[str, bytes] = {}
        self._stopping = False
        self._failure_count = 0
        self._last_error_log_at = 0.0
        self._thread: threading.Thread | None = None

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending)

    def start(self) -> None:
        if not self.token or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="baiamonte-can-ha-publisher",
            daemon=True,
        )
        self._thread.start()

    def queue(self, entity_id: str, payload: dict[str, object]) -> None:
        if not self.token:
            return
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        with self._condition:
            if encoded == self._last_sent.get(entity_id):
                return
            # A dict is deliberately used as a bounded, coalescing queue: one
            # newest payload per entity, regardless of bus or API speed.
            self._pending[entity_id] = encoded
            self._condition.notify()

    def stop(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _next(self) -> tuple[str, bytes] | None:
        with self._condition:
            while not self._pending and not self._stopping:
                self._condition.wait()
            if self._stopping:
                return None
            return self._pending.popitem()

    def _put_back_if_current(self, entity_id: str, payload: bytes) -> None:
        with self._condition:
            self._pending.setdefault(entity_id, payload)

    def _backoff(self) -> None:
        delay = min(30.0, 2.0 ** min(self._failure_count - 1, 5))
        with self._condition:
            if not self._stopping:
                self._condition.wait(timeout=delay)

    def _run(self) -> None:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        while True:
            item = self._next()
            if item is None:
                return
            entity_id, payload = item
            request = urllib.request.Request(
                f"{self.api_base}/{entity_id}",
                data=payload,
                method="POST",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.request_timeout):
                    pass
                with self._condition:
                    self._last_sent[entity_id] = payload
                    if self._pending.get(entity_id) == payload:
                        self._pending.pop(entity_id, None)
                self._failure_count = 0
                if self.error_callback:
                    self.error_callback(None)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._failure_count += 1
                message = f"Home Assistant state update failed: {exc}"
                now = time.monotonic()
                if self._failure_count == 1 or now - self._last_error_log_at >= 60:
                    self.log(f"{message}; background publisher will retry")
                    self._last_error_log_at = now
                if self.error_callback:
                    self.error_callback(message)
                self._put_back_if_current(entity_id, payload)
                self._backoff()
