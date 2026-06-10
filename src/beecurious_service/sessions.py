from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.registry import AgentProfileRegistry
from beecurious_service.providers import AgentProvider
from beecurious_service.telemetry import LokiTelemetry, TelemetryEvent


LOG = logging.getLogger(__name__)


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
    lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def session_id(self) -> str:
        """Backward-compatible alias for the public agent session identifier."""
        return self.agent_session_id

    def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate commands for one Fabric event payload."""
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

        commands = [command.to_dict() for command in result.commands]
        self._emit(
            "agent_commands_generated",
            interaction_id,
            {
                "event_type": event_type,
                "commands": commands,
                "response_id": result.response_id,
                "usage": result.usage,
                "model": result.model,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "agent_profile": self.profile.profile_id,
            },
        )
        return {"commands": commands, "interaction_id": interaction_id}

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
