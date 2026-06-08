from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4

from beecurious_service.prompts import build_instructions
from beecurious_service.providers import AgentProvider


@dataclass
class AgentSession:
    """Conversation state for one Fabric agent session."""
    session_id: str
    provider: AgentProvider
    previous_response_id: str | None = None
    lock: Lock = field(default_factory=Lock, repr=False)

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

        context = payload.get("context", "")
        if not isinstance(context, str):
            raise ValueError("context must be a string")

        with self.lock:
            result = self.provider.generate(
                instructions=build_instructions(event_type, context),
                event=event,
                previous_response_id=self.previous_response_id,
            )
            self.previous_response_id = result.response_id
        return {"commands": [command.to_dict() for command in result.commands]}


class SessionStore:
    """Thread-safe in-memory store for active agent sessions."""
    def __init__(self, provider: AgentProvider):
        self._provider = provider
        self._sessions: dict[str, AgentSession] = {}
        self._lock = Lock()

    def create(self) -> AgentSession:
        """Create and retain a new agent session."""
        session = AgentSession(str(uuid4()), self._provider)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> AgentSession | None:
        """Return an active session by identifier."""
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """Delete a session and report whether it existed."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None
