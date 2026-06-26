"""Central, Python-side feature toggles for Bip's behaviour.

This is the single control panel for *behavioural* features — how talkative Bip is, whether
he improvises, whether the interactive Q&A and clock hand-off beats run, and so on. The activity
choreography (see the per-activity modules) reads these flags to decide what Bip does.

Each flag is read once from the environment. The service already loads ``.env`` into the process
environment (see ``config.load_dotenv``), so setting e.g. ``BIP_BREVITY=false`` in that file is
enough to turn a feature off. Omitting a line, or any value other than a falsey one, leaves it on.

Note: the low-level *rendering* flags (idle wander, look-at-player, emotion particles, kick
reaction) live on the Java side in ``run/.env`` because they drive the mod's physics/animation,
not Bip's dialogue. They are intentionally not duplicated here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_FALSEY = {"false", "0", "no", "off", ""}


def _flag(key: str, default: bool = True) -> bool:
    """Read a boolean BIP_* toggle from the environment (default on)."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() not in _FALSEY


@dataclass(frozen=True)
class BipFeatures:
    """Behavioural feature flags, resolved once from the environment."""

    # Keep dialogue to a single short line; no padding or recap.
    brevity: bool
    # React only to the current event; never drift onto unrelated topics.
    stay_on_task: bool
    # Allow Bip to improvise narration + movement from an activity_context cue. Off = only the
    # verbatim activity_narration beats fire (fully scripted Bip).
    llm_improv: bool
    # Escalating nudge -> hint -> answer when the player stalls on a sub-task.
    tiered_hints: bool
    # Bip asks the player a reflective question and judges their chat answer ("Not quite" / confirm).
    interactive_qa: bool
    # Bip flies over and personally hands the player the time-travel clocks.
    time_travel_handover: bool
    # Verbatim scripted lines are fed into the LLM's memory as Bip's own prior turns, so he stays
    # coherent when the player chats (e.g. he "remembers" the scripted question he asked).
    scripted_memory: bool

    @classmethod
    def from_env(cls) -> "BipFeatures":
        return cls(
            brevity=_flag("BIP_BREVITY"),
            stay_on_task=_flag("BIP_STAY_ON_TASK"),
            llm_improv=_flag("BIP_LLM_IMPROV"),
            tiered_hints=_flag("BIP_TIERED_HINTS"),
            interactive_qa=_flag("BIP_INTERACTIVE_QA"),
            time_travel_handover=_flag("BIP_TIME_TRAVEL_HANDOVER"),
            scripted_memory=_flag("BIP_SCRIPTED_MEMORY"),
        )


@lru_cache(maxsize=1)
def get_features() -> BipFeatures:
    """Resolve the feature flags once, on first use.

    Lazy on purpose: the service loads ``.env`` inside ``main()`` at startup, which runs *after*
    module imports. Reading at import time would see only defaults, so we defer until first call
    (which happens while handling requests, well after the environment is populated).
    """
    return BipFeatures.from_env()
