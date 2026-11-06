"""Python-owned activity choreography for Bip.

The mod (Java) drives the pacing of each activity and reports *semantic* beats ("the player just
pollinated", "a flower withered", "the lever appeared"...) as ``activity_beat`` game events::

    {"event_type": "activity_beat",
     "details": {"activity": "pollinate", "beat": "buds", "flower_id": 12}}

This module owns what Bip actually does for each beat. Every beat is one of two kinds:

* **scripted** — a verbatim ``say`` (optionally with a ``fly_to``), spoken exactly, no LLM call.
* **improv** — an instruction handed to the LLM so Bip phrases it himself (with a scripted ``say``
  fallback used when ``BIP_LLM_IMPROV`` is off).

Activities, in tutorial order: ``intro`` -> ``observe`` (game 1) -> ``pollinate`` (game 2) ->
``recommend`` (game 3) -> ``diversify`` (game 4) -> ``actual`` (game 5, the real game). ``flow``
holds the lines between stages (portals, the post-tutorial menu, the base-1 restart item).

Placeholders: any ``{key}`` in a line is filled from the beat details Java sends (unknown keys are
left as-is), and a ``fly_to`` of ``("flower", "@id")`` flies to ``details["flower_id"]``.

Every spoken line follows the natural-tone rules (see tests/test_activities.py): one short
sentence or two, at most one "!", no semicolons or em dashes, no grown-up classroom phrasing.
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


def _say(line: str, *, fly_to: tuple[str, ...] | None = None) -> Beat:
    """A plain scripted line (instant, exact)."""
    return Beat(say=line, fly_to=fly_to, scripted_only=True)


_NUDGE_GUESS = "Just guess, I wanna hear what you think!"

_CLOCK_HANDOFF = Beat(
    requires="time_travel_handover",
    fly_to=("player",),
    say="Here, take these clocks! Right-click them to rewind and fast-forward the garden.",
    give_clocks=True,
    scripted_only=True,
)


# --- Introduction (right after /game new 3 and the consent screens) --------------------------
# Says what the whole game is for before any stage starts, and trades names.

_INTRO: dict[str, Beat] = {
    "hello": _say("Hey there! I'm Bip Buzzley, and this is the magic garden.",
                  fly_to=("player",)),
    "ask_name": _say("What's your name?"),
    "ask_name_nudge_0": _say("So, what should I call you?"),
    "ask_name_nudge_1": _say("You can type your name in chat, I'm all ears."),
    "purpose_garden": _say("This garden is special. It grows flowers based on what you pick."),
    "purpose_apps": _say("Kinda like how YouTube or TikTok pick videos for you."),
    "purpose_goal": _say("We'll find out how that works, and how to stay in charge of it."),
    "plan": _say("First we explore, then pollinate, then crack the garden's secret, then save it!"),
    "questions": _say("Any questions before we start? Just type in chat."),
    "go": _say("Alright, let's go!"),
}


# --- Game 1: Observe the flowers -----------------------------------------------------------
# Get to know what's in the garden: the data panel and what each number means. Then the player
# explores, picks flowers that look interesting, and asks questions. Nothing about pollinating yet.
#
# Detail keys Java provides:
#   flower_id            - the flower Bip flies to / the flower being talked about
#   color                - its color name, e.g. "Purple"
#   smell_word / nectar_word / water_word - its levels in words ("strong", "super sweet", "a lot of")
#   trait_label / trait_word - the trait that makes a suggested flower stand out, in words

_OBSERVE: dict[str, Beat] = {
    # Only when this stage is played without the introduction first (base-1 menu pick).
    "hello": _say("Hi, I'm Bip Buzzley! What's your name?", fly_to=("player",)),
    "hello_nudge_0": _say("So, what should I call you?"),
    "hello_nudge_1": _say("You can type your name in chat, I'm all ears."),

    "welcome": _say("Stage one! Let's get to know the flowers in this garden."),
    "show_flower": _say("See this one? Every flower here has its own secret info.",
                        fly_to=("flower", "@id")),
    "point_at": _say("Point your crosshair at a flower and look at the right side of your screen."),
    "point_nudge_0": _say("Just aim right at a flower, like this one by me.",
                          fly_to=("flower", "@id")),
    "point_nudge_1": _say("Look straight at any flower. Its info pops up on the right."),
    "panel_found": _say("That's the data panel! It's like the flower's ID card."),
    "panel_color": _say("Color is a number for its shade. This one's {color}."),
    "panel_smell": _say("Smell strength is how strong it smells. This one's is {smell_word}."),
    "panel_nectar": _say("Nectar sweetness is how sweet it is for bees. This one's {nectar_word}."),
    "panel_water": _say("Water needed is how thirsty it is. It needs {water_word} water."),
    "panel_sun": _say("Sunlight is how much sun it likes. That one's about the same everywhere."),
    "panel_number": _say("And the flower number is just its name tag."),
    "panel_meaning": _say("Every flower has its own mix of numbers. That's what makes it unique."),

    "explore_ask": _say("Now go find a flower that looks interesting to you. Point at it and "
                        "tell me!"),
    "explore_suggest_0": _say("Psst, what about this {color} one? Its {trait_label} is "
                              "{trait_word}.", fly_to=("flower", "@id")),
    "explore_suggest_1": _say("Ooh, or this {color} one over here. Its {trait_label} is "
                              "{trait_word}.", fly_to=("flower", "@id")),
    "explore_different": _say("Now can you find one that's totally different from that one?"),
    "different_suggest_0": _say("Hmm, this {color} one is pretty different. Check its numbers.",
                                fly_to=("flower", "@id")),
    "different_suggest_1": _say("Or look at this {color} one. Compare its panel to your first "
                                "pick.", fly_to=("flower", "@id")),
    "questions": _say("Any questions about the flowers? Ask me anything."),
    "questions_more": _say("Anything else you're curious about?"),
    "wrap": _say("Awesome, now you know what's in the garden."),
    "next_tease": _say("Next up, we get to pollinate them. It's gonna be fun!"),
    "time_travel": _say("Whoa, look how the garden changed!"),
}


# --- Game 2: Pollinate (the filter bubble effect) -------------------------------------------
# Builds on game 1 ("you can read the data panel"). Bip teaches pollinating himself, narrates
# every step of a pollination in order, introduces the diversity score, and hands over the clocks.
# The big "why" is saved for game 3, so Bip ends on a teaser.
#
# Detail keys: flower_id (fly_to target), color (a suggested flower's color name).

_POLLINATE: dict[str, Beat] = {
    "hello": _say("Hi, I'm Bip Buzzley! What's your name?", fly_to=("player",)),
    "hello_nudge_0": _say("So, what should I call you?"),
    "hello_nudge_1": _say("You can type your name in chat, I'm all ears."),

    "welcome": _say("Stage two! You already know how to read a flower's data panel.",
                    fly_to=("player",)),
    "recap": _say("Now let's do what bees do best. Pollinate!"),
    "teach_point": _say("Watch me. First, point your crosshair at a flower, like this {color} one.",
                        fly_to=("flower", "@id")),
    "teach_point_nudge_0": _say("Aim right at a flower so its data panel pops up."),
    "teach_point_nudge_1": _say("Just look at this flower by me.", fly_to=("flower", "@id")),
    "teach_slot": _say("See the little hive in slot five? Press 5 to hold it."),
    "teach_click": _say("Now, holding the hive, right-click the flower!"),
    "pollinate_reminder_0": _say("Hold the hive from slot five and right-click any flower."),
    "pollinate_reminder_1": _say("Pick any flower you like and right-click it with the hive."),

    # First pollination, narrated in the order things actually happen on screen.
    "first_pollinated": _say("You did it! Now watch the big beehive."),
    "hive_moved": _say("See? The hive flew over toward your flower.", fly_to=("beehive",)),
    "buds": _say("Look, baby buds popped up around it!", fly_to=("flower", "@id")),
    "numbers": _say("Each bud got a number. Number one is the hive's top pick."),
    "circle": _say("And that glowing circle shows where buds are allowed to grow."),
    "pollen": _say("See the pollen flying into the hive? It's gathering them up.",
                   fly_to=("beehive",)),
    "new_flowers": _say("Whoa, the top buds bloomed! They look a lot like your flower.",
                        fly_to=("flower", "@id")),
    "wither": Beat(
        say_before="Uh oh, look over there.",
        fly_to=("flower", "@id"),
        say="This flower faded away.",
        look_before=True,
        scripted_only=True,
    ),
    "why_dead_ask": _say("Why do you think it faded?"),
    "why_dead_nudge_0": _say("Any ideas? Look at where it was."),
    "why_dead_nudge_1": _say(_NUDGE_GUESS),
    "why_dead_hint": _say("Keep an eye on which flowers fade. It's a big clue."),
    "diversity_intro": _say("See the bar at the top of your screen? That's the diversity score."),
    "diversity_meaning": _say("It shows how many different kinds of flowers the garden has."),
    "diversity_dropped": _say("And it just went down a little. Hmm..."),
    "clock_handoff": _CLOCK_HANDOFF,
    "clock_handoff_no_reply": _CLOCK_HANDOFF,
    "try_clocks": _say("Try rewinding to see how the garden looked before."),
    "again": _say("Okay, let's pollinate another flower and see what happens."),

    # Round 2 and later.
    "again_pick": _say("Try a flower that looks totally different this time."),
    "buds_again": _say("More buds! Check out their numbers.", fly_to=("flower", "@id")),
    "new_flowers_again": _say("Hmm, the new ones look a lot like what you picked before.",
                              fly_to=("flower", "@id")),
    "diversity_check": _say("Check the diversity score. It keeps dropping..."),
    "keep_going_0": _say("Keep going, let's see where this goes."),
    "keep_going_1": _say("Pollinate another one. Watch the score."),

    # Game-level reactions (emitted by BeetrapStateManager).
    "time_travel": _say("See? The garden had way more kinds of flowers before."),
    "diversity_dropped_again": _say("Uh oh, the colors are fading again..."),

    # The garden's diversity fell below half: wrap up and bridge to game 3.
    "garden_died": _say("Uh oh, so many kinds of flowers are gone."),
    "diversity_end": _say("The diversity score dropped a ton!"),
    "bridge": _say("Why did that happen? I'll show you in the next stage."),
    "lets_go": _say("Let's go!"),
}


# --- Game 3: How does the garden recommend flowers? ----------------------------------------
# Bip is a GUIDE here (see GUIDE_STANCE in bip_v4/prompts.py): at each key moment he asks the
# player a thinking question (*_ask), Java waits for a chat answer and nudges (*_nudge_N) if none
# comes, and only then does Bip explain (*_explain). Round 3 is a Bip-led multiple-choice check:
# Bip asks aloud (quiz_q_<id>), Java shows the choice screen, Bip reacts (quiz_right_N /
# quiz_wrong_<id>). All beats are scripted_only so the teaching content is exact and instant.
#
# Detail keys Java provides per beat:
#   flower_id  - the flower a fly_to targets (pollinated flower, withered flower, new flower)

_RECOMMEND: dict[str, Beat] = {
    # --- Opening: get the player to the beehive
    "intro": _say("Hi, I'm Bip Buzzley! Wanna find out how this garden picks its flowers?"),
    "intro_returning": _say("Stage three! Remember how the flowers faded? Let's crack the secret."),
    "find_hive": _say("Come check out the beehive with me!", fly_to=("beehive",)),
    "find_hive_nudge_0": _say("It's over here, by me!"),
    "find_hive_nudge_1": _say("Over here! Just point at the beehive."),

    # --- At the hive: what do distances between flowers mean?
    "distance_ask": _say("Look around. Why do you think some flowers are close together?"),
    "distance_nudge_0": _say("Any guesses? Check two neighbors on the data panel."),
    "distance_nudge_1": _say(_NUDGE_GUESS),
    "distance_explain": _say("Flowers with similar numbers on the data panel grow close together!"),
    "go_pollinate": _say("Okay, now pick a flower you like and pollinate it."),
    "pollinate_reminder_0": _say("Which one's your favorite? Try pollinating it!"),
    "pollinate_reminder_1": _say("Pick any flower and pollinate it. Let's see what happens!"),

    # --- Round 1: the bud numbers, then what goes into the hive
    "numbers_ask": _say("Remember the numbers on the buds? What do you think they rank?",
                        fly_to=("flower", "@id")),
    "numbers_nudge_0": _say("What do you think the numbers are counting?"),
    "numbers_nudge_1": _say(_NUDGE_GUESS),
    "numbers_explain": _say("They rank how close each bud is to your hive. Number one's the "
                            "closest!"),
    "hive_ask": _say("See the pollen flying into the hive? What do you think it's collecting?",
                     fly_to=("beehive",)),
    "hive_nudge_0": _say("What's the hive gathering, you think?"),
    "hive_nudge_1": _say(_NUDGE_GUESS),
    "hive_explain": _say("The flowers you pollinate! The hive's learning what you like."),
    "clock_handoff": _CLOCK_HANDOFF,
    "clock_handoff_no_reply": _CLOCK_HANDOFF,

    # --- Round 2: the pollen circle, then why flowers wither
    "circle_ask": _say("See that glowing circle again? What do you think it's for?"),
    "circle_nudge_0": _say("What happens inside that circle, you think?"),
    "circle_nudge_1": _say(_NUDGE_GUESS),
    "circle_explain": _say("New buds can only pop up inside that circle."),
    "wither_ask": Beat(
        say_before="Uh oh, look over there.",
        fly_to=("flower", "@id"),
        say="That one died. Notice where it was?",
        look_before=True,
        scripted_only=True,
    ),
    "wither_nudge_0": _say("Was it close to your hive, or far away?"),
    "wither_nudge_1": _say(_NUDGE_GUESS),
    "wither_explain": _say("It was way far from what you like, so it faded."),

    # --- Later rounds and game-level reactions (emitted by BeetrapStateManager)
    "new_flowers": _say("Look, more flowers just like the ones you picked.",
                        fly_to=("flower", "@id")),
    "time_travel": _say("See? It had way more kinds of flowers before. Why do you think it "
                        "changed?"),
    "diversity_dropped_again": _say("Uh oh, the colors are fading again..."),

    # --- Round 3: Bip-led multiple-choice check
    "quiz_intro": _say("Quick quiz time! Let's see what we figured out."),
    "quiz_q_user_profile": _say("So, what goes into the beehive?"),
    "quiz_q_flower_buds_on_ground": _say("What do the buds on the ground stand for?"),
    "quiz_q_numbers_above_flower_buds": _say("What do the numbers above the buds mean?"),
    "quiz_q_pollen_circle": _say("And what's that pollen circle for?"),
    "quiz_q_flowers_located_in_garden": _say("Last one! How are the flowers arranged in the "
                                             "garden?"),
    "quiz_right_0": _say("Yes! You got it."),
    "quiz_right_1": _say("Yep, exactly right!"),
    "quiz_right_2": _say("Nailed it!"),
    "quiz_wrong_user_profile": _say("Close! It's actually the flowers you pollinated."),
    "quiz_wrong_flower_buds_on_ground": _say("Not quite. They're the flowers that could grow "
                                             "next."),
    "quiz_wrong_numbers_above_flower_buds": _say("Almost! They rank how close each bud is to your "
                                                 "hive."),
    "quiz_wrong_pollen_circle": _say("Not quite. It's where new flowers are allowed to grow."),
    "quiz_wrong_flowers_located_in_garden": _say("Close! Flowers that are alike in lots of ways "
                                                 "sit together."),

    # --- Ending: the garden lost its colors
    "wrap_ask": _say("So... why do you think the garden lost its colors?"),
    "wrap_nudge_0": _say("What do you think made the colors fade?"),
    "wrap_nudge_1": _say(_NUDGE_GUESS),
    "wrap_reveal": _say("Your hive is kinda like your profile. It remembers what you pick."),
    "wrap_recommender": _say("So the garden keeps showing you more of the same. That's a "
                             "recommender system!"),
    "wrap_bubble": _say("Getting stuck with only the same stuff is called a filter bubble."),
    "complete": _say("You cracked the secret! Next, let's figure out how to save the garden."),
}


# --- Game 4: Break the filter bubble -------------------------------------------------------
# Follows the Diversification rows of the "Make Bip Great Again" sheet: explain the goal (diversity
# above a target), hint that the pollen circle is the key, press B near it to grow it, a lever pops
# up at radius 5 that switches the hive to pick DIFFERENT flowers, and celebrate when the target is
# beaten.
#
# Detail keys: target (the diversity score to beat), flower_id.

_DIVERSIFY: dict[str, Beat] = {
    "hello": _say("Hi, I'm Bip Buzzley! What's your name?", fly_to=("player",)),
    "hello_nudge_0": _say("So, what should I call you?"),
    "hello_nudge_1": _say("You can type your name in chat, I'm all ears."),

    "welcome": _say("Last stage! This garden's already stuck in a bubble.", fly_to=("player",)),
    "look": _say("Look around. These flowers are all kinda the same.", fly_to=("beehive",)),
    "recall": _say("Remember, the hive keeps picking flowers close to what you like."),
    "goal": _say("Our mission is to get the diversity score above {target}!"),
    "goal_bar": _say("The bar at the top shows how close we are."),
    "hint_circle": _say("Hmm, remember the pollen circle? I bet it's the key."),
    "go": _say("Pollinate any flower and let's experiment."),
    "pollinate_reminder_0": _say("Pick any flower and pollinate it, let's try stuff."),
    "pollinate_reminder_1": _say("Hold the hive from slot five and right-click a flower."),

    # First pollination: grow the circle, then the lever.
    "press_b": _say("Get close to the glowing circle and press B lots of times.",
                    fly_to=("beehive",)),
    "press_b_nudge_0": _say("Stand near the circle, then keep tapping B. Watch it grow!"),
    "press_b_nudge_1": _say("Keep pressing B, it grows a tiny bit each time."),
    "circle_grew": _say("Whoa, the circle got bigger! More kinds of buds fit in now."),
    "lever_appeared": _say("A lever just popped up over here!", fly_to=("lever",)),
    "lever_ask": _say("What do you think it does? Try flicking it."),
    "lever_nudge_0": _say("Flick the lever by me, let's see what happens."),
    "lever_nudge_1": _say("Right-click the lever to flip it."),
    "lever_on": _say("Now the hive picks buds that are different from what you like!"),
    "lever_off": _say("Oops, that's back to the old way. Flip it again?"),

    "bloom_check": _say("Look at the diversity score now!"),
    "why_ask": _say("Why do you think it went up this time?"),
    "why_nudge_0": _say("What did we change from before?"),
    "why_nudge_1": _say(_NUDGE_GUESS),
    "why_explain": _say("A bigger circle plus picking different flowers breaks the bubble!"),
    "keep_going_0": _say("Nice, keep pollinating different flowers."),
    "keep_going_1": _say("Keep it up, we're getting closer."),
    "almost": _say("We're so close to {target}, keep going!"),
    "time_travel": _say("Rewind and compare. See how the mix changed?"),
    "diversity_dropped_again": _say("Hmm, the score dipped. Try a different kind of flower."),

    "success": _say("You did it! You broke the bubble and saved the garden."),
    "lesson": _say("Apps work the same way. Mix up what you watch to keep your feed colorful."),
    "complete": _say("That's the whole tutorial. You're a garden expert now!"),
}


# --- Game 5: the actual game ---------------------------------------------------------------
# After the tutorial. The player leads; Bip cheers, notices and helps when asked.

_ACTUAL: dict[str, Beat] = {
    "welcome": _say("This is the real garden now. No more training!", fly_to=("player",)),
    "goal": _say("Pollinate whatever you like, and keep an eye on the diversity score."),
    "help": _say("I'm right here if you need me. Just ask."),
    "round_react": Beat(
        say="Ooh, let's see what changed this time.",
        improv=(
            "A pollination round just finished in the real game. Look at the game state (diversity "
            "score, new and faded flowers) and in one short line react to what changed. If the "
            "score dropped a lot, remind them of a trick from the tutorial."
        ),
    ),
    "time_travel": _say("Nice, comparing the garden over time like a pro."),
    "diversity_dropped_again": _say("Careful, the bubble's creeping back."),
}


# --- Between stages --------------------------------------------------------------------------

_FLOW: dict[str, Beat] = {
    # Base 2: a portal opens at the end of each tutorial stage.
    "portal_open": _say("A portal just opened! Hop in when you're ready for the next stage.",
                        fly_to=("portal",)),
    "portal_nudge": _say("The portal's right here whenever you're ready.", fly_to=("portal",)),
    "tutorial_done": _say("You finished the tutorial! Replay a stage or start the real game."),
    # Base 1: the restart item takes the player back to the stage menu.
    "next_stage_item": _say("When you're ready, use the barrier in slot 7 to pick the next "
                            "stage."),
}


ACTIVITIES: dict[str, dict[str, Beat]] = {
    "intro": _INTRO,
    "observe": _OBSERVE,
    "pollinate": _POLLINATE,
    "recommend": _RECOMMEND,
    "diversify": _DIVERSIFY,
    "actual": _ACTUAL,
    "flow": _FLOW,
}


def resolve(activity: str, beat: str, features) -> Beat | None:
    """Resolve a (activity, beat) to a concrete Beat, honouring feature flags."""
    table = ACTIVITIES.get(activity)
    if table is None:
        return None
    resolved = table.get(beat)
    if resolved is None:
        return None
    if resolved.requires and not getattr(features, resolved.requires, True):
        return None
    return resolved
