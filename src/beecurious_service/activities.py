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
    # FB1: greet, invite them to take in how varied the garden is, then a prediction prompt.
    # scripted_only so Bip speaks the instant the session connects (no LLM wait at game start).
    "intro": Beat(
        say="Hi, I'm Bip Buzzley! Look how many colors and different flowers there are. Pollinate "
            "one you like, and let's see what happens!",
        scripted_only=True,
    ),
    # Already met the player in an earlier activity — skip the greeting, just set up the task.
    "intro_returning": Beat(
        say="Okay, let's pollinate a flower and see what happens to the garden!",
        scripted_only=True,
    ),
    # FB2: full how-to (used when tiered hints are off; repeated on a timer while the player idles).
    "instructions": Beat(
        say="Aim at a flower and the bee nest pops into slot 5. Hold it and right-click to pollinate!",
    ),
    # Stuck on sub-task 1: hasn't aimed at a flower yet.
    "stuck_look_0": Beat(say="See a flower you like? Point your crosshair right at it."),
    "stuck_look_1": Beat(say="Aim straight at any flower in the garden, go on, pick one!"),
    # Stuck on sub-task 2: aimed at a flower but hasn't pollinated.
    "stuck_pollinate_0": Beat(
        say="The bee nest should be in slot 5. Hold it and right-click the flower to pollinate!"),
    "stuck_pollinate_1": Beat(
        say="Select slot 5, aim at the flower, then right-click to pollinate it!"),
    # FB3 + FB4: buds have sprouted, each showing a small number. Point out the buds, that the hive
    # ranks them by how close they are (number 1 = closest), and that some will grow. This is the
    # observable mechanic, so Bip MAY explain it; he just doesn't explain WHY it matters.
    "buds_ranked": Beat(
        say="See the new buds with numbers? Number 1 is the closest to the flower you pollinated. "
            "Some of them will grow!",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — don't make the player wait on the LLM here
        improv=(
            "New flower buds just sprouted, and each shows a small number above it. Fly down next "
            "to the bud labelled 1 (id {flower_id}) and explain in one short line that the hive "
            "ranks the buds by how close they are, so number 1 is the closest to the flower they "
            "pollinated, and some will grow. You SHOULD mention the numbers, since the buds visibly "
            "show them. Do not explain why it matters."
        ),
    ),
    # FB7 (notice): the bloom happened — new flowers grew. Only celebrate the growth here; the
    # deaths are left for why_dead so Bip doesn't repeat the same observation two beats in a row.
    "new_flowers": Beat(
        say="Whoa, look! New flowers just bloomed!",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — point it out the instant they bloom
        improv=(
            "The pollination just bloomed and new flowers grew (one is id {flower_id}). Fly to the "
            "new flower and, in one short excited line, celebrate that new flowers just appeared. "
            "Do NOT mention anything dying yet — that comes in the very next beat. Call it "
            "\"this one\", never a number."
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
    # interactive_qa off: notice it, call them over, dart to it, then share a HUNCH (not a fact).
    "why_dead_tell": Beat(
        say_before="Oh no, that one died, come over here!",
        fly_to=("flower", "@id"),
        say="Hmm. I wonder if it's because so many flowers here look the same now? Weird, huh?",
        look_before=True,  # glance at the dead flower, react, THEN dart over
        improv=(
            "A flower (id {flower_id}) just withered. Say one short dismayed line that calls the "
            "player over, then fly to it, then wonder OUT LOUD, as a tentative hunch, whether it "
            "happened because so many flowers here look the same now. Do NOT state it as a known "
            "fact or mention any system. Never say its number — call it \"that one\" / \"this one\"."
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
        say="Whoa, look! It was way more colorful before. And the ones that vanished were the really "
            "different ones. Huh, why do you think that is?",
        improv=(
            "The player just rewound the garden with the clocks. In one short, slightly wistful "
            "line, notice OUT LOUD that it used to be more colorful and that the flowers that "
            "vanished were the really different ones, and wonder why that might be — as a curious "
            "observation, not a known explanation, then ask what the player thinks."
        ),
    ),
}


# --- Observe the Flowers --------------------------------------------------------------------
# Bip introduces the garden and the player's role, then teaches that a flower is just its five
# attributes (color, smell, nectar, water, sunlight) and that "different" literally means "far
# apart". Java drives the phases and fills the {placeholders} below from the real flower it flew
# to, so even the scripted lines name the actual values the player sees on the sidebar.
#
# Detail keys Java provides per beat:
#   flower_id          - the flower the fly_to/look_at targets (focus, neighbor, or the pick)
#   attributes         - human phrase for the focus flower, e.g. "Red petals, very sweet nectar, ..."
#   neighbor_attributes- human phrase for the neighbor flower (compare_neighbor)
#   requested          - what to go find, e.g. "a Dark blue one" (challenge)
#   distance_desc      - how different the pick turned out, e.g. "really different" (distance_lesson)

_OBSERVE: dict[str, Beat] = {
    # Greet + set up the role. scripted_only so Bip speaks the instant the session connects.
    "intro": Beat(
        say="Hi, I'm Bip Buzzley, and this is the magic garden! You're the gardener, and with my "
            "help we'll keep it alive and colorful.",
        scripted_only=True,
    ),
    # Already met the player in an earlier activity — skip the greeting, just set up the task.
    "intro_returning": Beat(
        say="Okay, let's take a closer look at these flowers together!",
        scripted_only=True,
    ),
    # Fly to a focus flower and ask the player to read it off (interactive_qa ON).
    "examine": Beat(
        say="Come look at this flower! Put your crosshair on it and tell me what you see.",
        fly_to=("flower", "@id"),
        improv=(
            "Fly down to this flower (id {flower_id}). It has: {attributes}. In ONE short, curious "
            "line, ask the player to aim at it and tell you what they notice. Do NOT list the "
            "attributes yourself yet — let them look first. Call it \"this flower\", never a number."
        ),
    ),
    # interactive_qa OFF: don't ask, just fly over and notice what it has out loud.
    "examine_tell": Beat(
        say="Ooh, come look at this flower! I see {attributes}.",
        fly_to=("flower", "@id"),
        improv=(
            "Fly down to this flower (id {flower_id}) and, like you're noticing it together, point "
            "out in one short line what you can see: {attributes}. Call it \"this flower\", never a "
            "number."
        ),
    ),
    # No answer yet — warmly nudge them to look and describe (fires up to MAX_ANSWER_NUDGES).
    "examine_nudge": Beat(
        say="Aim right at the flower I'm next to, and tell me what stands out about it!",
        improv=(
            "The player still hasn't described the flower. In one short line, warmly nudge them to "
            "aim at it and tell you what they notice."
        ),
    ),
    # Dart to the most-similar neighbor and notice how alike they are — plant the "why?" as a
    # question, never as a stated rule.
    "compare_neighbor": Beat(
        say="Huh, look at this one right next to it! It has {neighbor_attributes}. Almost the same, "
            "right? I wonder if that's why they're neighbors.",
        fly_to=("flower", "@id"),
        improv=(
            "Fly to this neighboring flower (id {flower_id}), which has {neighbor_attributes}. In "
            "one short, surprised line, notice out loud how similar it is to the one you just "
            "looked at, and wonder aloud if that's why they sit next to each other. Do not state it "
            "as a rule. Call it \"this one\", never a number."
        ),
    ),
    # Set the challenge: go find something genuinely different.
    "challenge": Beat(
        say="Now, can you find me a really different flower? Try {requested}. Aim at it and "
            "right-click to pick it!",
        improv=(
            "Challenge the player, in one short line, to go find a flower that is really different "
            "from the ones you just looked at — suggest {requested} — and to aim at it and "
            "right-click to pick it."
        ),
    ),
    # Still hasn't picked — nudge them to choose one.
    "challenge_nudge": Beat(
        say="Look around the garden for a flower that's really different, then right-click to pick it!",
        improv=(
            "The player still hasn't picked a flower. In one short line, warmly nudge them to find "
            "a really different flower and right-click it."
        ),
    ),
    # The payoff: the flower they picked. Land the distance/difference idea as a shared HUNCH the
    # player is invited to confirm, not a fact Bip already knew.
    "distance_lesson": Beat(
        say="Nice pick! And whoa, this one was {distance_desc} from the first, way over here. Do "
            "you think the really different flowers always end up far apart?",
        fly_to=("flower", "@id"),
        improv=(
            "The player picked this flower (id {flower_id}); compared to the first flower it is "
            "{distance_desc} and sits far from it. Fly to it and, in one or two short lines, "
            "wonder OUT LOUD whether really different flowers always end up far apart while similar "
            "ones sit close — as a curious hunch you just noticed, and ask what the player thinks. "
            "Do NOT state it as a known rule or mention any system. Call it \"this one\", never a "
            "number."
        ),
    ),
}


ACTIVITIES: dict[str, dict[str, Beat]] = {
    "pollinate": _POLLINATE,
    "observe": _OBSERVE,
}


def resolve(activity: str, beat: str, features) -> Beat | None:
    """Resolve a (activity, beat) to a concrete Beat, honouring feature flags."""
    table = ACTIVITIES.get(activity)
    if table is None:
        return None

    # interactive_qa off -> swap the "ask the player" beats for the "just tell them" variants.
    if not features.interactive_qa:
        if beat == "why_dead":
            beat = "why_dead_tell"
        elif beat == "examine":
            beat = "examine_tell"

    resolved = table.get(beat)
    if resolved is None:
        return None
    if resolved.requires and not getattr(features, resolved.requires, True):
        return None
    return resolved
