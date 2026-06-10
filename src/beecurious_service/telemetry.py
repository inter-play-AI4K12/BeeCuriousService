from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from queue import Full, Queue
import ssl
from threading import Thread
import time
from typing import Any
from urllib import error, request

import certifi


LOG = logging.getLogger(__name__)
USER_AGENT = "BeeCuriousService/0.1"


class PermanentLokiError(OSError):
    """A Loki response that should not be retried."""


@dataclass(frozen=True)
class TelemetryEvent:
    event_type: str
    game_session_id: str
    agent_session_id: str | None = None
    interaction_id: str | None = None
    participant_id: str | None = None
    data: dict[str, Any] | None = None


class LokiTelemetry:
    """Asynchronously sends structured BeeCurious events to Loki."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str | None,
        queue_size: int = 1000,
        timeout_seconds: float = 5.0,
    ):
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds
        self._queue: Queue[TelemetryEvent] = Queue(maxsize=queue_size)
        ca_bundle = os.getenv("SSL_CERT_FILE") or certifi.where()
        self._ssl_context = ssl.create_default_context(cafile=ca_bundle)
        self._worker: Thread | None = None

        if self.enabled:
            self._worker = Thread(
                target=self._run,
                name="beecurious-loki",
                daemon=True,
            )
            self._worker.start()

    @property
    def enabled(self) -> bool:
        return bool(self._password)

    def emit(self, event: TelemetryEvent) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(event)
        except Full:
            LOG.warning(
                "Loki telemetry queue is full; dropping event_type=%s",
                event.event_type,
            )

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                self._send_with_retry(event)
            finally:
                self._queue.task_done()

    def _send_with_retry(self, event: TelemetryEvent) -> None:
        for attempt in range(3):
            try:
                self._send(event)
                return
            except PermanentLokiError as exc:
                LOG.warning(
                    "Failed to send Loki event_type=%s: %s",
                    event.event_type,
                    exc,
                )
                return
            except (error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    LOG.warning(
                        "Failed to send Loki event_type=%s after retries: %s",
                        event.event_type,
                        exc,
                    )
                    return
                time.sleep(0.25 * (2**attempt))

    def _send(self, event: TelemetryEvent) -> None:
        timestamp = datetime.now(timezone.utc)
        record: dict[str, Any] = {
            "schema_version": 1,
            "timestamp": timestamp.isoformat(),
            "app": "beetrap",
            "source": "beecurious-service",
            "event_type": event.event_type,
            "game_session_id": event.game_session_id,
        }
        if event.agent_session_id:
            record["agent_session_id"] = event.agent_session_id
        if event.interaction_id:
            record["interaction_id"] = event.interaction_id
        if event.participant_id:
            record["participant_id"] = event.participant_id
        if event.data is not None:
            record["data"] = event.data

        timestamp_ns = int(timestamp.timestamp() * 1_000_000_000)
        payload = {
            "streams": [
                {
                    "stream": {
                        "app": "beetrap",
                        "source": "beecurious-service",
                    },
                    "values": [
                        [
                            str(timestamp_ns),
                            json.dumps(record, separators=(",", ":"), ensure_ascii=True),
                        ]
                    ],
                }
            ]
        }
        credentials = f"{self._username}:{self._password}"
        http_request = request.Request(
            self._url + "/loki/api/v1/push",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Basic "
                + base64.b64encode(credentials.encode("utf-8")).decode("ascii"),
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with request.urlopen(
                http_request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                if not 200 <= response.status < 300:
                    raise OSError(f"Loki returned HTTP {response.status}")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            message = f"Loki returned HTTP {exc.code}: {details}"
            if 400 <= exc.code < 500 and exc.code not in {408, 429}:
                raise PermanentLokiError(message) from exc
            raise OSError(message) from exc
