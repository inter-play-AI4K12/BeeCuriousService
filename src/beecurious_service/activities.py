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
# Detail keys Java provides per beat:
#   flower_id  - the flower a fly_to targets (suggested flower, pollinated flower, new flower...)
#   color      - human color name of the suggested flower (suggest_flower)

_POLLINATE: dict[str, Beat] = {
    # FB1: greet. scripted_only so Bip speaks the instant the session connects (no LLM wait at
    # game start). Wording mirrors the case where the player has NOT met Bip yet.
    "intro": Beat(
        say="Hi, I'm Bip Buzzley, and welcome to the magic garden! Let's try pollinating some "
            "flowers!",
        scripted_only=True,
    ),
    # Already met the player in an earlier activity (the normal flow, since Observe runs first) —
    # skip the greeting, just set up the task.
    "intro_returning": Beat(
        say="This time, let's try pollinating some flowers!",
        scripted_only=True,
    ),
    # FB2: Bip proactively suggests a specific nearby flower and flies to it, naming its color from
    # {color}. scripted_only for reliability (the color must always be right).
    "suggest_flower": Beat(
        say="Did you see that? Let's find a flower you really like! What do you think about this "
            "{color} one?",
        fly_to=("flower", "@id"),
        scripted_only=True,
    ),
    # Two escalating, unconditional reminders (not gated on whether they've aimed at anything) —
    # ~15s and ~30s after suggest_flower if the player still hasn't pollinated.
    "pollinate_reminder_0": Beat(say="Come on, try pollinating your favorite flower!"),
    "pollinate_reminder_1": Beat(
        say="What's your favorite flower? Try pollinating it and we'll see what happens!"),
    # FB3 + FB4: buds have sprouted, each showing a small number. Fly to the flower the player just
    # pollinated and wonder OUT LOUD what the numbers mean, rather than explaining it or asking a
    # direct question — this beat never waits for or reacts to an answer (the bloom happens on a
    # fixed timer right after), so phrasing it as a wonder rather than a question the player is
    # expected to answer avoids the awkward "asked, then ignored the answer" feeling.
    "buds_ranked": Beat(
        say="Look, new buds have grown! I wonder what the numbers mean.",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — don't make the player wait on the LLM here
        improv=(
            "New flower buds just sprouted next to the flower the player pollinated (id "
            "{flower_id}), each showing a small number above it. Fly down to it and, in one short "
            "curious line, wonder OUT LOUD what the numbers might mean. Do NOT phrase it as a "
            "direct question aimed at the player (you will not wait for or react to a reply), and "
            "do not explain it yourself."
        ),
    ),
    # FB7 (notice): the bloom happened — new flowers grew, and they resemble the one just planted.
    # Only celebrate the growth+resemblance here; the deaths are left for why_dead so Bip doesn't
    # repeat the same observation two beats in a row.
    "new_flowers": Beat(
        say="Ooh wow! Look, some new flowers grew! They look a lot like the ones you planted!",
        fly_to=("flower", "@id"),
        scripted_only=True,  # immediate feedback — point it out the instant they bloom
        improv=(
            "The pollination just bloomed and new flowers grew (one is id {flower_id}), and they "
            "resemble the flower the player planted. Fly to the new flower and, in one short "
            "excited line, celebrate that new flowers grew AND notice they look a lot like the "
            "one just planted. Do NOT mention anything dying yet — that comes in the very next "
            "beat. Call it \"this one\", never a number."
        ),
    ),
    # FB4.1: the hive/nest itself moved toward the pollinated flower — a separate, purely factual
    # beat fired a few seconds after new_flowers so it never lands back-to-back with it.
    "hive_moved": Beat(
        say="And it looks like the hive moved as well to get closer to the flower you "
            "pollinated!",
        scripted_only=True,
    ),
    # Ask the player to reason about a death (interactive_qa on): notice it, call them over, dart
    # to it, then ask. "that one" (not "this one") because Bip isn't there yet when he reacts.
    "why_dead": Beat(
        say_before="Oh no! Look over there, that flower died!",
        fly_to=("flower", "@id"),
        say="Why do you think this flower died?",
        look_before=True,  # glance at the dead flower, react, THEN dart over
        improv=(
            "A flower (id {flower_id}) just withered and died. Say one short dismayed line that "
            "calls the player over (like \"Oh no! Look over there, that flower died!\"), then fly "
            "to it, then ask in one short question why they think this flower died. Do NOT answer "
            "it yourself. Never say the flower's number — call it \"that one\" / \"this one\"."
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
        say_before="Oh no! Look over there, that flower died!",
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
    # Bip physically hands over the time-travel clocks (after the why-dead exchange). Never fires
    # until the player has actually answered (see FilterBubbleBipScriptedPollinationHappeningState
    # PHASE_ASK/PHASE_REFINE — Java gates this beat behind a real reply, not a timer).
    "clock_handoff": Beat(
        requires="time_travel_handover",
        fly_to=("player",),
        say="Here, have these clocks! Use them to look at how the garden changed over time!",
        improv=(
            "Fly to the player and give them two clock tools that rewind and replay the garden. "
            "In one short line, tell them to right-click to use them and to watch how the garden "
            "changed over time."
        ),
        give_clocks=True,  # the clocks land in hand right as the say finishes
    ),
    # Degraded-path variant of clock_handoff: fires ONLY when Bip's reply to the why-dead answer
    # never arrived within a generous window (LLM/network hiccup). scripted_only so it is always
    # reliable — the whole point is to never hand over the clocks in total silence.
    "clock_handoff_no_reply": Beat(
        requires="time_travel_handover",
        fly_to=("player",),
        say="Anyway, here, have these clocks! Use them to look at how the garden changed over "
            "time!",
        give_clocks=True,
        scripted_only=True,
    ),
    # The player just rewound the garden — this is where the reflection lands, because they can
    # actually SEE the before/after. Deliberately avoids the word "diversity" (Bip is an
    # intelligent novice; this stays an observable, wondered-about comparison, not a named metric).
    "time_travel": Beat(
        say="Whoa, it was way more colorful and varied before you started pollinating! Why do you "
            "think that happened?",
        improv=(
            "The player just rewound the garden with the clocks. In one short, slightly wistful "
            "line, notice OUT LOUD that it used to be more colorful and varied before they started "
            "pollinating, and ask what the player thinks happened. Do not name any system or "
            "metric — keep it an observation, not an explanation."
        ),
    ),
    # Fires once, the first time a LATER pollination round ends with less color/variety than the
    # round before (see BeetrapStateManager) — a one-shot echo of the time_travel beat so repeated
    # rounds don't all narrate identically.
    "diversity_dropped_again": Beat(
        say="Aw man, it looks less colorful and varied again!",
        scripted_only=True,
    ),
    # The activity ends because the garden's color/variety fell too low to continue.
    "garden_died": Beat(
        say="Oh no! It looks like the garden got a lot less colorful, and it ended up dying!",
        scripted_only=True,
    ),
}


# --- Observe the Flowers --------------------------------------------------------------------
# Bip opens by trading names with the player, sets up the shared goal, points out the data panel,
# then runs a short "find a flower with this trait" search — repeated with a different trait each
# round — before handing off to the Filter Bubble activity. Java drives the phases and fills the
# {placeholders} below from the real flower/feature it is working with.
#
# Detail keys Java provides per beat:
#   requested      - what to go find, e.g. "a Purple flower" / "a flower with a strong smell"
#   feature_label  - the exact sidebar row name for the current target, e.g. "Nectar sweetness"

_OBSERVE: dict[str, Beat] = {
    # Step 0: first-ever meeting — ask the player's name. scripted_only so Bip speaks the instant
    # the session connects. The reply is handled by the ordinary chat pipeline: Bip's own question
    # is remembered (scripted_memory), so when the player answers, the LLM naturally greets them by
    # name (see NAME_GREETING_RULE in prompts.py) without any extra plumbing here.
    "intro": Beat(
        say="Hi, my name's Bip Buzzley! What's yours?",
        scripted_only=True,
    ),
    # First-ever meeting, but interactive_qa is off — no question, just a plain warm greeting.
    "intro_no_qa": Beat(
        say="Hi, I'm Bip Buzzley! Welcome to the magic garden!",
        scripted_only=True,
    ),
    # Already met the player in an earlier activity (e.g. Filter Bubble ran first) — skip the
    # name exchange entirely, just jump into looking at flowers together.
    "intro_returning": Beat(
        say="Okay, let's take a closer look at these flowers together!",
        scripted_only=True,
    ),
    # No reply to "what's your name?" yet after ~15s.
    "ask_name_nudge": Beat(
        say="Can you tell me your name?",
        scripted_only=True,
    ),
    # Sets up the shared goal once the name exchange has settled.
    "role_intro": Beat(
        say="You're a bee, just like me! Our goal is to keep the garden colorful by pollinating "
            "flowers!",
        scripted_only=True,
    ),
    # Names the pattern explicitly — deliberately breaks from this persona's usual "intelligent
    # novice" restraint (see BIP_V4_PERSONA/NOVICE_RULE), at explicit direction: middle schoolers
    # already encounter recommender systems everywhere, so naming it up front is the intended design
    # for this activity specifically.
    "recommender_intro": Beat(
        say="This is kind of like a recommender system, actually. Do you know what that means?",
        scripted_only=True,
    ),
    # interactive_qa off: state it plainly, no question, no wait.
    "recommender_intro_no_qa": Beat(
        say="This is kind of like a recommender system, the kind you see all over the internet!",
        scripted_only=True,
    ),
    # No reply yet after ~15s.
    "recommender_nudge": Beat(
        say="Ever heard of a recommender system before? Take a guess!",
        scripted_only=True,
    ),
    # Round 0 of the search task. Folds in the "did you see that" acknowledgement of the hover
    # tutorial text screen Java just showed, so it doesn't need its own separate say.
    "search_task": Beat(
        say="Did you see that? Let's try and look at some other flowers. Find {requested}, then "
            "right-click it!",
        scripted_only=True,
    ),
    # Rounds 1+ of the search task, when the player DID share their reasoning for the last pick.
    "search_task_next": Beat(
        say="Nice! Now find {requested} and right-click it!",
        scripted_only=True,
    ),
    # Rounds 1+ when the player did NOT answer the reflection question — no "Nice!" opener, since
    # that would falsely imply they'd just explained something.
    "search_task_next_no_reply": Beat(
        say="That's okay! Let's find another one — {requested}, then right-click it!",
        scripted_only=True,
    ),
    # Hasn't picked anything in a while — nudge toward the current target.
    "search_nudge": Beat(
        say="Still looking? Try to find {requested} and right-click it when you spot it!",
        scripted_only=True,
    ),
    # Right-clicked a flower that matches the current target.
    "search_success": Beat(
        say="Nice! That looks perfect. What in the data panel told you this was the right choice?",
        scripted_only=True,
    ),
    # Right-clicked a flower that does NOT match — sends them back to their own data panel rather
    # than stating the answer. Deliberately an instruction, NOT a question: the ordinary chat
    # pipeline judges any reply the player types with no visibility into the actual target, so it
    # would happily agree with a wrong reading ("yep, that sounds right!") even though the pick was
    # still wrong — asking "what does it show?" invites exactly that contradiction. Two variants so
    # a retry doesn't repeat verbatim.
    "search_failure_0": Beat(
        say="Hmm, not quite. Take another look at the {feature_label} on the data panel, then try "
            "again!",
        scripted_only=True,
    ),
    "search_failure_1": Beat(
        say="Let's pause. Check the {feature_label} for that one closely, then give it another "
            "try!",
        scripted_only=True,
    ),
    # All rounds complete — wrap up. The next-steps instructions are shown as a text screen
    # alongside this beat (see ObserveFlowersBipState), not spoken.
    "complete": Beat(
        say="Perfect, that was great! See you soon!",
        scripted_only=True,
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
        elif beat == "intro" and activity == "observe":
            beat = "intro_no_qa"

    resolved = table.get(beat)
    if resolved is None:
        return None
    if resolved.requires and not getattr(features, resolved.requires, True):
        return None
    return resolved
