from collections import deque
from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.registry import AgentProfileRegistry
from beecurious_service.models import AgentCommand
from beecurious_service.providers import AgentProvider
from beecurious_service.telemetry import LokiTelemetry, TelemetryEvent


LOG = logging.getLogger(__name__)
STATIONARY_SECONDS = 60.0
STATIONARY_HISTORY_SECONDS = 75.0
STATIONARY_TOLERANCE = 0.1
MAX_SNAPSHOT_HISTORY = 120
MAX_COMMAND_HISTORY = 500


@dataclass(frozen=True)
class PositionSample:
    received_at: float
    game_tick: int
    position: tuple[float, float, float]


@dataclass
class TrackedCommand:
    command: AgentCommand
    state: str = "issued"


@dataclass
class AgentSession:
    """Agent state and telemetry correlation for one Fabric agent session."""
    agent_session_id: str
    game_session_id: str
    participant_id: str | None
    telemetry_enabled: bool
    profile: AgentProfile
    provider: AgentProvider
    telemetry: LokiTelemetry
    previous_response_id: str | None = None
    stationary_alerted: bool = False
    snapshots: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_SNAPSHOT_HISTORY),
        repr=False,
    )
    positions: deque[PositionSample] = field(
        default_factory=lambda: deque(maxlen=MAX_SNAPSHOT_HISTORY),
        repr=False,
    )
    commands: dict[str, TrackedCommand] = field(default_factory=dict, repr=False)
    command_order: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_COMMAND_HISTORY),
        repr=False,
    )
    lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def session_id(self) -> str:
        """Backward-compatible alias for the public agent session identifier."""
        return self.agent_session_id

    def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate commands or process a lightweight agent heartbeat."""
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValueError("event must be an object")

        event = {str(key).lower(): value for key, value in event.items()}
        event_type = event.get("event_type")
        if not isinstance(event_type, str):
            raise ValueError("event.event_type must be a string")
        event_type = event_type.lower()
        event["event_type"] = event_type
        LOG.info(
            "Handling event session=%s profile=%s event_type=%s",
            self.session_id,
            self.profile.profile_id,
            event_type,
        )

        if event_type == "agent_tick":
            return self._handle_agent_tick(event)

        context = payload.get("context", "")
        if not isinstance(context, str):
            raise ValueError("context must be a string")

        interaction_id = str(uuid4())
        self._emit(
            "agent_event_received",
            interaction_id,
            {
                "event": event,
                "context": context,
                "agent_profile": self.profile.profile_id,
            },
        )
        started = time.monotonic()
        try:
            with self.lock:
                result = self.provider.generate(
                    instructions=self.profile.build_instructions(event_type, context),
                    event=event,
                    previous_response_id=self.previous_response_id,
                )
                self.previous_response_id = result.response_id
                commands = self._issue_commands(result.commands)
        except Exception as exc:
            self._emit(
                "agent_event_failed",
                interaction_id,
                {
                    "event_type": event_type,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise

        self._emit_commands(
            interaction_id,
            event_type,
            commands,
            model=result.model,
            response_id=result.response_id,
            usage=result.usage,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return {"commands": commands, "interaction_id": interaction_id}

    def _handle_agent_tick(self, event: dict[str, Any]) -> dict[str, Any]:
        snapshot = event.get("snapshot")
        execution = event.get("execution")
        if not isinstance(snapshot, dict):
            raise ValueError("agent_tick.snapshot must be an object")
        if not isinstance(execution, dict):
            raise ValueError("agent_tick.execution must be an object")

        with self.lock:
            self._record_snapshot(snapshot)
            failed_ids = self._reconcile_execution(execution)
            new_commands: list[dict[str, Any]] = []
            if self.profile.stationary_check:
                new_commands = self._stationary_player_commands()
            outstanding = self._outstanding_commands(execution)

        self._emit(
            "game_state",
            None,
            {
                "snapshot": snapshot,
                "execution": execution,
                "agent_profile": self.profile.profile_id,
            },
        )
        if failed_ids:
            self._emit(
                "agent_commands_failed",
                None,
                {
                    "command_ids": failed_ids,
                    "game_tick": snapshot.get("game_tick"),
                    "agent_profile": self.profile.profile_id,
                },
            )
        if new_commands:
            self._emit_commands(
                None,
                "agent_tick",
                new_commands,
                model="bip3-stationary-memory-check",
            )
        return {"commands": outstanding}

    def _record_snapshot(self, snapshot: dict[str, Any]) -> None:
        game_tick = snapshot.get("game_tick")
        if not isinstance(game_tick, int):
            raise ValueError("agent_tick.snapshot.game_tick must be an integer")
        current_diversity = snapshot.get("current_diversity")
        if (
            not isinstance(current_diversity, int | float)
            or isinstance(current_diversity, bool)
        ):
            raise ValueError(
                "agent_tick.snapshot.current_diversity must be a number"
            )
        events = snapshot.get("events")
        if not isinstance(events, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("event_type"), str)
            and isinstance(item.get("game_tick"), int)
            and isinstance(item.get("details"), dict)
            for item in events
        ):
            raise ValueError(
                "agent_tick.snapshot.events must be a list of game events"
            )

        normalized = dict(snapshot)
        normalized["received_at"] = time.time()
        self.snapshots.append(normalized)

        player = snapshot.get("player")
        if not isinstance(player, dict):
            raise ValueError("agent_tick.snapshot.player must be an object")
        position = self._parse_position(player.get("position"))
        if position is None:
            raise ValueError(
                "agent_tick.snapshot.player.position must contain three numbers"
            )

        now = time.monotonic()
        self.positions.append(PositionSample(now, game_tick, position))
        while (
            self.positions
            and now - self.positions[0].received_at > STATIONARY_HISTORY_SECONDS
        ):
            self.positions.popleft()

    def _reconcile_execution(self, execution: dict[str, Any]) -> list[str]:
        current = self._optional_command_id(execution.get("current_command_id"))
        queued = self._command_id_list(
            execution.get("queued_command_ids"),
            "queued_command_ids",
        )
        completed = self._command_id_list(
            execution.get("completed_command_ids"),
            "completed_command_ids",
        )
        failed = self._command_id_list(
            execution.get("failed_command_ids"),
            "failed_command_ids",
        )

        for command_id in ([current] if current else []) + queued:
            tracked = self.commands.get(command_id)
            if tracked and tracked.state not in {"completed", "failed"}:
                tracked.state = "scheduled"
        for command_id in completed:
            tracked = self.commands.get(command_id)
            if tracked:
                tracked.state = "completed"
        for command_id in failed:
            tracked = self.commands.get(command_id)
            if tracked:
                tracked.state = "failed"
        return failed

    def _stationary_player_commands(self) -> list[dict[str, Any]]:
        stationary = self._is_stationary_for_window()
        if not stationary:
            self.stationary_alerted = False
            return []
        if self.stationary_alerted:
            return []

        self.stationary_alerted = True
        return self._issue_commands(
            [
                AgentCommand(
                    "say",
                    ["You've been still a while. Want to explore somewhere new?"],
                )
            ]
        )

    def _is_stationary_for_window(self) -> bool:
        if len(self.positions) < 2:
            return False
        samples = list(self.positions)
        if samples[-1].received_at - samples[0].received_at < STATIONARY_SECONDS:
            return False

        origin = samples[0].position
        return all(
            abs(sample.position[index] - origin[index]) <= STATIONARY_TOLERANCE
            for sample in samples
            for index in range(3)
        )

    def _issue_commands(
        self,
        commands: list[AgentCommand],
    ) -> list[dict[str, Any]]:
        issued: list[dict[str, Any]] = []
        for command in commands:
            identified = command.issued()
            command_id = identified.command_id
            if command_id is None:
                raise AssertionError("issued command is missing command_id")
            self.commands[command_id] = TrackedCommand(identified)
            self.command_order.append(command_id)
            issued.append(identified.to_dict())
        self._trim_command_history()
        return issued

    def _outstanding_commands(
        self,
        execution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        current = self._optional_command_id(execution.get("current_command_id"))
        queued = set(
            self._command_id_list(
                execution.get("queued_command_ids"),
                "queued_command_ids",
            )
        )
        acknowledged = queued | ({current} if current else set())
        return [
            self.commands[command_id].command.to_dict()
            for command_id in self.command_order
            if command_id in self.commands
            and self.commands[command_id].state == "issued"
            and command_id not in acknowledged
        ]

    def _trim_command_history(self) -> None:
        retained = set(self.command_order)
        for command_id in list(self.commands):
            if command_id not in retained:
                del self.commands[command_id]

    def _emit_commands(
        self,
        interaction_id: str | None,
        event_type: str,
        commands: list[dict[str, Any]],
        *,
        model: str | None,
        response_id: str | None = None,
        usage: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if not commands:
            return
        data: dict[str, Any] = {
            "event_type": event_type,
            "commands": commands,
            "model": model,
            "agent_profile": self.profile.profile_id,
        }
        if response_id:
            data["response_id"] = response_id
        if usage is not None:
            data["usage"] = usage
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        self._emit("agent_commands_generated", interaction_id, data)

    def _emit(
        self,
        event_type: str,
        interaction_id: str | None,
        data: dict[str, Any],
    ) -> None:
        if not self.telemetry_enabled:
            return
        self.telemetry.emit(
            TelemetryEvent(
                event_type=event_type,
                game_session_id=self.game_session_id,
                agent_session_id=self.agent_session_id,
                interaction_id=interaction_id,
                participant_id=self.participant_id,
                data=data,
            )
        )

    def retrieve_logs(
        self,
        *,
        event_type: str | None = None,
        interaction_id: str | None = None,
        hours: int = 24,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve Loki records correlated to this exact agent session."""
        return self.telemetry.retrieve_session_logs(
            self.agent_session_id,
            self.game_session_id,
            event_type=event_type,
            interaction_id=interaction_id,
            hours=hours,
            limit=limit,
        )

    @staticmethod
    def _parse_position(value: object) -> tuple[float, float, float] | None:
        if not isinstance(value, list) or len(value) != 3:
            return None
        if not all(isinstance(item, int | float) for item in value):
            return None
        return (float(value[0]), float(value[1]), float(value[2]))

    @staticmethod
    def _optional_command_id(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("current_command_id must be a string or null")
        return value or None

    @staticmethod
    def _command_id_list(value: object, name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"{name} must be a list of strings")
        return list(dict.fromkeys(value))


class SessionStore:
    """Thread-safe in-memory store for active agent sessions."""
    def __init__(
        self,
        provider: AgentProvider,
        profile_registry: AgentProfileRegistry,
        default_agent_id: str,
        default_agent_version: str,
        telemetry: LokiTelemetry,
    ):
        self._provider = provider
        self._profile_registry = profile_registry
        self._default_agent_id = default_agent_id
        self._default_agent_version = default_agent_version
        self._telemetry = telemetry
        self._sessions: dict[str, AgentSession] = {}
        self._lock = Lock()

    def create(self, request: dict[str, Any] | None = None) -> AgentSession:
        """Resolve a profile, then create and retain a pinned agent session."""
        request = request or {}
        game_session_id = request.get("game_session_id")
        participant_id = request.get("participant_id")
        telemetry_enabled = request.get("logging_consent", False)
        requested_agent_id = request.get("agent", self._default_agent_id)
        requested_version = request.get("version", self._default_agent_version)
        if not isinstance(game_session_id, str) or not game_session_id.strip():
            raise ValueError("game_session_id must be a non-empty string")
        if participant_id is not None and not isinstance(participant_id, str):
            raise ValueError("participant_id must be a string")
        if not isinstance(telemetry_enabled, bool):
            raise ValueError("logging_consent must be a boolean")
        if not isinstance(requested_agent_id, str):
            raise ValueError("agent must be a string")
        if not isinstance(requested_version, str):
            raise ValueError("version must be a string")

        profile = self._profile_registry.resolve(
            requested_agent_id,
            requested_version,
        )
        session = AgentSession(
            agent_session_id=str(uuid4()),
            game_session_id=game_session_id.strip(),
            participant_id=participant_id.strip() if participant_id else None,
            telemetry_enabled=telemetry_enabled,
            profile=profile,
            provider=self._provider,
            telemetry=self._telemetry,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        session._emit(
            "agent_session_created",
            None,
            {"agent_profile": profile.profile_id},
        )
        return session

    def get(self, session_id: str) -> AgentSession | None:
        """Return an active session by identifier."""
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """Delete a session and report whether it existed."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session._emit(
            "agent_session_closed",
            None,
            {"agent_profile": session.profile.profile_id},
        )
        return True
