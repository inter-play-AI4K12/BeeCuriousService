from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.bip_v2.prompts import build_instructions


BIP_V2_PROFILE = AgentProfile(
    agent_id="bip",
    version="2.0",
    display_name="Bip Buzzley",
    build_instructions=build_instructions,
)
