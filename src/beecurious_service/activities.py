"""Python-owned activity choreography for Bip.

The mod (Java) reports *semantic* beats — "pollination just ended", "a flower withered", a
stuck player, etc. — by emitting an ``activity_beat`` game event of the form::

    {"event_type": "activity_beat",
     "details": {"activity": "pollinate", "beat": "ranking", "flower_id": 12}}

This module owns what Bip actually does for each beat. Every beat is one of two kinds (the
"mesh" of scripted + LLM):

* **scripted** — a verbatim ``say`` (optionally with a ``fly_to``), spoken exactly, no LLM call.
* **improv** — an instruction handed to the LLM so Bip phrases it himself and moves around.

Each improv beat also carries a scripted ``say`` used as a deterministic fallback when
``BIP_LLM_IMPROV`` is off, so turning improv off yields a fully-scripted (but coherent) Bip.

Feature flags (features.py) shape resolution: ``interactive_qa`` swaps the "ask why" beat for a
"tell why" one; ``time_travel_handover`` gates the clock hand-off; ``llm_improv`` chooses
improv vs the scripted fallback.

Placeholders: an improv description may contain ``{flower_id}`` (filled from the event details),
and a scripted ``fly_to`` of ``("flower", "@id")`` flies to ``details["flower_id"]``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Beat:
    say: str | None = None            # verbatim line; also the fallback when improv is off
    say_before: str | None = None     # spoken BEFORE the fly_to (e.g. "Oh no!" then fly over)
    fly_to: tuple[str, ...] | None = None
    improv: str | None = None         # LLM instruction, used when BIP_LLM_IMPROV is on
    requires: str | None = None       # feature-flag name; beat is skipped when that flag is off
    give_clocks: bool = False         # append a give_clocks command AFTER the say (synced hand-off)
    scripted_only: bool = False       # always use the instant scripted line (skip the LLM) — for
                                      # quick-feedback beats where a 3-4s "thinking" pause feels broken
    look_before: bool = False         # turn to face the fly_to target before speaking, then fly over


# --- Pollinate the Garden -------------------------------------------------------------------

_POLLINATE: dict[str, Beat] = {
    "intro": Beat(
        say="Hi, I'm Bip! Pollinate any flower you like and let's see what happens!",
    ),
    # Full how-to (used when tiered hints are off; repeated on a timer while the player is idle).
    "instructions": Beat(
        say="Aim at a flower, then right-click with the pollen in slot 5 to pollinate it.",
    ),
    # Stuck on sub-task 1: hasn't aimed at a flower yet.
    "stuck_look_0": Beat(say="See a flower you like? Point your crosshair right at it."),
    "stuck_look_1": Beat(say="Aim straight at any flower in the garden, go on, pick one!"),
    # Stuck on sub-task 2: aimed at a flower but hasn't pollinated.
    "stuck_pollinate_0": Beat(
        say="The pollen should show up in slot 5, right-click with it to pollinate!"),
    "stuck_pollinate_1": Beat(
        say="Select slot 5, then right-click while aiming at the flower to pollinate it!"),
    # Buds have sprouted and each visibly shows a small rank number. ONE beat (not two) so it plays
    # before the bloom without backing up the queue. Here Bip SHOULD talk about the numbers.
    "buds_ranked": Beat(
        say="See how the buds have numbers over them? Number 1 is the most like the one you pollinated!",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — don't make the player wait on the LLM here
        improv=(
            "New flower buds just sprouted, and each shows a small number above it. Fly down next "
            "to the bud labelled 1 (id {flower_id}) and explain in one short line that those "
            "numbers are a ranking — number 1 is the bud most similar to the flower the player "
            "just pollinated. You SHOULD mention the numbers, since the buds visibly show them."
        ),
    ),
    # Fly over and point out a freshly grown flower.
    "new_flowers": Beat(
        say="Ooh, new flowers grew! See how they look a lot like the one you pollinated?",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — point it out the instant they bloom
        improv=(
            "New flowers grew from the pollination, and they resemble the flower the player "
            "pollinated. Fly to the flower with id {flower_id} and point out, in one short, "
            "delighted line, that the new flowers look a lot like the one they chose. Never say "
            "its number — call it \"this one\"."
        ),
    ),
    # Ask the player to reason about a death (interactive_qa on): notice it, call them over, dart
    # to it, then ask. "that one" (not "this one") because Bip isn't there yet when he reacts.
    "why_dead": Beat(
        say_before="Oh no, that one died, come over here!",
        fly_to=("flower", "@id"),
        say="Why do you think it happened?",
        look_before=True,  # glance at the dead flower, react, THEN dart over
        improv=(
            "A flower (id {flower_id}) just withered and died. Say one short dismayed line that "
            "calls the player over (like \"Oh no, that one died, come over here!\"), then fly to "
            "it, then ask in one short question why they think it died. Do NOT answer it yourself. "
            "Never say the flower's number — call it \"that one\" / \"this one\"."
        ),
    ),
    # No answer yet — warmly pressure the player to take a guess (fires up to MAX_ANSWER_NUDGES).
    "why_dead_nudge": Beat(
        say="Come on, take a guess! Why do you think that flower died?",
        improv=(
            "The player still hasn't answered why the flower died. In one short line, warmly "
            "pressure them to take a guess. Do NOT answer it for them."
        ),
    ),
    # interactive_qa off: notice it, call them over, dart to it, then explain it yourself.
    "why_dead_tell": Beat(
        say_before="Oh no, that one died, come over here!",
        fly_to=("flower", "@id"),
        say="It died because too many similar flowers crowded out the different kinds.",
        look_before=True,  # glance at the dead flower, react, THEN dart over
        improv=(
            "A flower (id {flower_id}) just withered. Say one short dismayed line that calls the "
            "player over, then fly to it, then explain in one short line that it died because too "
            "many similar flowers crowded out the different kinds. Never say its number — call it "
            "\"that one\" / \"this one\"."
        ),
    ),
    # Bip physically hands over the time-travel clocks (after the why-dead exchange).
    "clock_handoff": Beat(
        requires="time_travel_handover",
        fly_to=("player",),
        say="Here, take these clocks! Rewind the garden and see what happens to all the colors.",
        improv=(
            "Fly to the player and give them two clock tools that rewind and replay the garden. "
            "In one short line, tell them to right-click to use them and to watch what happens "
            "to the garden's colors and variety."
        ),
        give_clocks=True,  # the clocks land in hand right as the say finishes
    ),
    # The player just rewound the garden — this is where the diversity reflection lands, because
    # they can actually SEE the before/after. (There is no separate "closing" line any more.)
    "time_travel": Beat(
        say="See? The garden was more colorful before, the flowers that died were the most different ones.",
        improv=(
            "The player just rewound the garden with the clocks. In one short, slightly wistful "
            "line, point out that the garden used to be more colorful and varied, and that the "
            "flowers that died were the ones most different from what they kept pollinating."
        ),
    ),
}


ACTIVITIES: dict[str, dict[str, Beat]] = {
    "pollinate": _POLLINATE,
}


def resolve(activity: str, beat: str, features) -> Beat | None:
    """Resolve a (activity, beat) to a concrete Beat, honouring feature flags."""
    table = ACTIVITIES.get(activity)
    if table is None:
        return None

    # interactive_qa off -> swap the "ask why" beat for the "tell why" beat.
    if beat == "why_dead" and not features.interactive_qa:
        beat = "why_dead_tell"

    resolved = table.get(beat)
    if resolved is None:
        return None
    if resolved.requires and not getattr(features, resolved.requires, True):
        return None
    return resolved
