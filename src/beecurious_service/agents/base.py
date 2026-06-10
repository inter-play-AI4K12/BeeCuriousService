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

    @property
    def profile_id(self) -> str:
        """Return the stable identifier used in logs and API responses."""
        return f"{self.agent_id}@{self.version}"
