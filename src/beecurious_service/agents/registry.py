from collections.abc import Iterable

from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.bip_v1.profile import BIP_V1_PROFILE
from beecurious_service.agents.bip_v2.profile import BIP_V2_PROFILE
from beecurious_service.agents.bip_v3.profile import BIP_V3_PROFILE


class AgentProfileRegistry:
    """Registry of immutable agent profiles available to new sessions."""

    def __init__(self, profiles: Iterable[AgentProfile]):
        self._profiles = {
            (profile.agent_id, profile.version): profile for profile in profiles
        }

    def resolve(self, agent_id: str, version: str) -> AgentProfile:
        """Resolve an exact profile or reject the unknown identifier."""
        normalized_key = (agent_id.strip().lower(), version.strip())
        profile = self._profiles.get(normalized_key)
        if profile is None:
            raise ValueError(
                f"unknown agent profile: {normalized_key[0]}@{normalized_key[1]}"
            )
        return profile


def create_agent_registry() -> AgentProfileRegistry:
    """Create the registry of agent profiles shipped with this service."""
    return AgentProfileRegistry([BIP_V1_PROFILE, BIP_V2_PROFILE, BIP_V3_PROFILE])
