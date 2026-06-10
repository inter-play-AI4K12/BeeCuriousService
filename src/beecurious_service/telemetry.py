from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from queue import Full, Queue
import ssl
from threading import Thread
import time
from typing import Any
from urllib import error, parse, request

import certifi


LOG = logging.getLogger(__name__)
USER_AGENT = "BeeCuriousService/0.1"


class PermanentLokiError(OSError):
    """A Loki response that should not be retried."""


class LokiNotConfiguredError(RuntimeError):
    """Raised when a Loki query is attempted without credentials."""


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

    def retrieve_session_logs(
        self,
        agent_session_id: str,
        game_session_id: str,
        *,
        event_type: str | None = None,
        interaction_id: str | None = None,
        hours: int = 24,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve structured logs belonging to one exact active agent session."""
        if not self.enabled:
            raise LokiNotConfiguredError("Loki telemetry is not configured")

        # Make events emitted by this process visible before querying the current session.
        self._queue.join()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        agent_fragment = json.dumps(
            f'"agent_session_id":"{agent_session_id}"',
            ensure_ascii=True,
        )
        logql = (
            '{app="beetrap"}'
            f" |= {agent_fragment}"
        )
        payload = self._query_range(logql, start, end, limit)

        records: list[dict[str, Any]] = []
        for result in payload.get("data", {}).get("result", []):
            for timestamp_ns, line in result.get("values", []):
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("agent_session_id") != agent_session_id:
                    continue
                if record.get("game_session_id") != game_session_id:
                    continue
                if event_type and record.get("event_type") != event_type:
                    continue
                if interaction_id and record.get("interaction_id") != interaction_id:
                    continue
                record["timestamp_ns"] = timestamp_ns
                records.append(record)

        records.sort(
            key=lambda record: int(record.get("timestamp_ns", 0)),
            reverse=True,
        )
        return records[:limit]

    def retrieve_game_logs(
        self,
        game_session_id: str,
        *,
        source: str,
        event_type: str,
        hours: int = 1,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve one event stream correlated by an exact game session ID."""
        if not self.enabled:
            raise LokiNotConfiguredError("Loki telemetry is not configured")

        self._queue.join()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        game_fragment = json.dumps(
            f'"game_session_id":"{game_session_id}"',
            ensure_ascii=True,
        )
        event_fragment = json.dumps(
            f'"event_type":"{event_type}"',
            ensure_ascii=True,
        )
        logql = (
            f'{{app="beetrap",source="{source}"}}'
            f" |= {game_fragment}"
            f" |= {event_fragment}"
        )
        payload = self._query_range(logql, start, end, limit)
        records: list[dict[str, Any]] = []
        for result in payload.get("data", {}).get("result", []):
            for timestamp_ns, line in result.get("values", []):
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("game_session_id") != game_session_id:
                    continue
                if record.get("source") != source:
                    continue
                if record.get("event_type") != event_type:
                    continue
                record["timestamp_ns"] = timestamp_ns
                records.append(record)

        records.sort(
            key=lambda record: int(record.get("timestamp_ns", 0)),
            reverse=True,
        )
        return records[:limit]

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
        http_request = request.Request(
            self._url + "/loki/api/v1/push",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": self._authorization_header(),
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

    def _authorization_header(self) -> str:
        credentials = f"{self._username}:{self._password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return "Basic " + encoded

    def _query_range(
        self,
        logql: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> dict[str, Any]:
        query = parse.urlencode(
            {
                "query": logql,
                "start": str(int(start.timestamp() * 1_000_000_000)),
                "end": str(int(end.timestamp() * 1_000_000_000)),
                "limit": str(limit),
                "direction": "backward",
            }
        )
        http_request = request.Request(
            self._url + "/loki/api/v1/query_range?" + query,
            headers={
                "Authorization": self._authorization_header(),
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with request.urlopen(
                http_request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OSError(f"Loki returned HTTP {exc.code}: {details}") from exc
        except json.JSONDecodeError as exc:
            raise OSError("Loki returned invalid JSON") from exc
