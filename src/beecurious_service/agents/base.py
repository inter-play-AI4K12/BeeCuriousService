from dataclasses import dataclass
from typing import Callable


InstructionBuilder = Callable[[str, str], str]


@dataclass(frozen=True)
class AgentProfile:
    """Immutable behavior definition for one version of an agent."""

    agent_id: str
    version: str
    display_name: str
    build_instructions: InstructionBuilder
    stationary_check: bool = False
    # This profile drives its own opener/closer via activity beats (intro/closing), so the
    # game_start/game_end LLM call is redundant. Skipping it also keeps the single command channel
    # free at startup, so the scripted intro fires immediately instead of after a multi-second call.
    beat_driven: bool = False

    @property
    def profile_id(self) -> str:
        """Return the stable identifier used in logs and API responses."""
        return f"{self.agent_id}@{self.version}"
