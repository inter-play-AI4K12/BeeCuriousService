from collections import deque
from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any
from uuid import uuid4

from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.registry import AgentProfileRegistry
from beecurious_service.models import AgentCommand
from beecurious_service.providers import AgentProvider
from beecurious_service.telemetry import LokiTelemetry, TelemetryEvent


LOG = logging.getLogger(__name__)
STATIONARY_SECONDS = 60.0
STATIONARY_HISTORY_SECONDS = 75.0
STATIONARY_TOLERANCE = 0.1
MAX_SNAPSHOT_HISTORY = 120
MAX_COMMAND_HISTORY = 500
# How many recent scripted lines to carry into the next LLM call as Bip's own prior turns. Caps
# growth if Bip runs fully scripted for a long stretch without the model ever being called.
MAX_SCRIPTED_MEMORY = 20

# Game events that should trigger an immediate LLM reaction when they arrive
# inside an agent_tick heartbeat (rather than being recorded silently).
# Note: player kicks are handled instantly on the mod side (no LLM), so they
# are intentionally not listed here. activity_context (improv) is gated by the
# BIP_LLM_IMPROV feature flag — see reactive_event_types().
REACTIVE_EVENT_TYPES = {"activity_narration", "activity_context"}


def reactive_event_types() -> set[str]:
    """Event types we react to, honouring the current feature flags."""
    from beecurious_service.features import get_features

    types = {"activity_narration"}
    if get_features().llm_improv:
        types.add("activity_context")
    return types


class _SafeFormat(dict):
    """dict for str.format_map that leaves unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass(frozen=True)
class PositionSample:
    received_at: float
    game_tick: int
    position: tuple[float, float, float]


@dataclass
class TrackedCommand:
    command: AgentCommand
    state: str = "issued"


@dataclass
class AgentSession:
    """Agent state and telemetry correlation for one Fabric agent session."""
    agent_session_id: str
    game_session_id: str
    participant_id: str | None
    telemetry_enabled: bool
    profile: AgentProfile
    provider: AgentProvider
    telemetry: LokiTelemetry
    previous_response_id: str | None = None
    stationary_alerted: bool = False
    snapshots: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_SNAPSHOT_HISTORY),
        repr=False,
    )
    positions: deque[PositionSample] = field(
        default_factory=lambda: deque(maxlen=MAX_SNAPSHOT_HISTORY),
        repr=False,
    )
    commands: dict[str, TrackedCommand] = field(default_factory=dict, repr=False)
    command_order: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_COMMAND_HISTORY),
        repr=False,
    )
    # Scripted lines Bip has spoken since the last LLM call. Injected as prior assistant turns on
    # the next call so the model "remembers" saying them (see _generate / _record_scripted_line).
    scripted_lines_pending: list[str] = field(default_factory=list, repr=False)
    lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def session_id(self) -> str:
        """Backward-compatible alias for the public agent session identifier."""
        return self.agent_session_id

    def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate commands or process a lightweight agent heartbeat."""
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValueError("event must be an object")

        event = {str(key).lower(): value for key, value in event.items()}
        event_type = event.get("event_type")
        if not isinstance(event_type, str):
            raise ValueError("event.event_type must be a string")
        event_type = event_type.lower()
        event["event_type"] = event_type
        LOG.info(
            "Handling event session=%s profile=%s event_type=%s",
            self.session_id,
            self.profile.profile_id,
            event_type,
        )

        if event_type == "agent_tick":
            return self._handle_agent_tick(event)

        # Beat-driven Bip greets via the scripted intro beat, so the game_start LLM call is pure
        # dead weight — and because every event shares one serialized command channel, that call
        # would block the scripted intro for several seconds at startup. Skip it entirely.
        if event_type == "game_start" and self.profile.beat_driven:
            return {"commands": [], "interaction_id": str(uuid4())}

        context = payload.get("context", "")
        if not isinstance(context, str):
            raise ValueError("context must be a string")

        interaction_id = str(uuid4())
        self._emit(
            "agent_event_received",
            interaction_id,
            {
                "event": event,
                "context": context,
                "agent_profile": self.profile.profile_id,
            },
        )
        started = time.monotonic()
        try:
            with self.lock:
                result = self._generate(
                    self.profile.build_instructions(event_type, context),
                    event,
                )
                commands = self._issue_commands(result.commands)
        except Exception as exc:
            self._emit(
                "agent_event_failed",
                interaction_id,
                {
                    "event_type": event_type,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise

        self._emit_commands(
            interaction_id,
            event_type,
            commands,
            model=result.model,
            response_id=result.response_id,
            usage=result.usage,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return {"commands": commands, "interaction_id": interaction_id}

    def _handle_agent_tick(self, event: dict[str, Any]) -> dict[str, Any]:
        snapshot = event.get("snapshot")
        execution = event.get("execution")
        if not isinstance(snapshot, dict):
            raise ValueError("agent_tick.snapshot must be an object")
        if not isinstance(execution, dict):
            raise ValueError("agent_tick.execution must be an object")

        with self.lock:
            self._record_snapshot(snapshot)
            failed_ids = self._reconcile_execution(execution)
            new_commands: list[dict[str, Any]] = []
            if self.profile.stationary_check:
                new_commands = self._stationary_player_commands()
            new_commands.extend(self._reactive_event_commands(snapshot))
            outstanding = self._outstanding_commands(execution)

        if failed_ids:
            self._emit(
                "agent_commands_failed",
                None,
                {
                    "command_ids": failed_ids,
                    "game_tick": snapshot.get("game_tick"),
                    "agent_profile": self.profile.profile_id,
                },
            )
        if new_commands:
            self._emit_commands(
                None,
                "agent_tick",
                new_commands,
                model="bip3-stationary-memory-check",
            )
        return {"commands": outstanding}

    def _reactive_event_commands(
        self,
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Invoke the LLM for notable game events carried by a heartbeat.

        Events such as a player kick or scripted activity narration only ever
        reach the service inside an agent_tick. Without this, the agent would
        never see them. We forward the reactive events to the provider so the
        agent profile can respond (e.g. yell when kicked, speak narration).
        """
        game_events = snapshot.get("game_events") or []
        allowed = reactive_event_types()

        commands: list[dict[str, Any]] = []
        legacy: list[dict[str, Any]] = []
        for event in game_events:
            event_type = event.get("event_type")
            if event_type == "activity_beat":
                # Python owns the choreography for these (scripted or improv per beat).
                commands.extend(self._activity_beat_commands(event))
            elif event_type in allowed:
                legacy.append(event)

        if legacy:
            commands.extend(self._legacy_reactive_commands(legacy))
        return commands

    def _legacy_reactive_commands(
        self,
        reactive: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Old path: forward verbatim activity_narration / activity_context to the LLM."""
        event = {
            "event_type": reactive[0]["event_type"],
            "game_events": reactive,
        }
        try:
            result = self._generate(
                self.profile.build_instructions(event["event_type"], ""),
                event,
            )
        except Exception:
            LOG.exception(
                "Failed to generate reactive command for %s",
                event["event_type"],
            )
            return []

        return self._issue_commands(result.commands)

    def _activity_beat_commands(
        self,
        event: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Turn one semantic activity_beat into commands via the Python beat registry."""
        from beecurious_service import activities
        from beecurious_service.features import get_features

        details = event.get("details") or {}
        activity = details.get("activity")
        beat_name = details.get("beat")
        if not isinstance(activity, str) or not isinstance(beat_name, str):
            return []

        features = get_features()
        beat = activities.resolve(activity, beat_name, features)
        if beat is None:
            return []

        if beat.improv and features.llm_improv and not beat.scripted_only:
            description = beat.improv.format_map(_SafeFormat(details))
            improv_event = {
                "event_type": "activity_context",
                "game_events": [
                    {
                        "event_type": "activity_context",
                        "details": {"description": description},
                    }
                ],
            }
            try:
                result = self._generate(
                    self.profile.build_instructions("activity_context", ""),
                    improv_event,
                )
            except Exception:
                LOG.exception("Failed to improvise beat %s/%s", activity, beat_name)
                return []
            improv_commands = list(result.commands)
            if beat.look_before:
                flower_id = details.get("flower_id")
                if flower_id is not None:
                    # Turn to the flower before the improvised reaction, then the LLM's fly_to.
                    improv_commands.insert(
                        0, AgentCommand("look_at", ["flower", str(int(flower_id))]))
            if beat.give_clocks:
                # Sequenced last so the items land exactly when Bip finishes the spoken hand-off.
                improv_commands.append(AgentCommand("give_clocks", []))
            return self._issue_commands(improv_commands)

        # Scripted (or improv fallback when llm_improv is off).
        # Order: look_at -> say_before ("Oh no!") -> fly over -> say -> give_clocks (if any).
        # Each spoken line is also recorded so the next LLM call knows Bip said it.
        # Scripted lines may carry live game values via {placeholders} (e.g. a flower's real
        # attributes), filled from the event details — so even fully-scripted Bip can state
        # what the player is actually looking at without an LLM call.
        commands: list[AgentCommand] = []
        fly_to = self._resolve_fly_to(beat.fly_to, details)
        say_before = self._fill(beat.say_before, details)
        say = self._fill(beat.say, details)
        if beat.look_before and fly_to:
            commands.append(AgentCommand("look_at", fly_to))
        if say_before:
            commands.append(AgentCommand("say", [say_before]))
            self._record_scripted_line(say_before)
        if fly_to:
            commands.append(AgentCommand("fly_to", fly_to))
        if say:
            commands.append(AgentCommand("say", [say]))
            self._record_scripted_line(say)
        if beat.give_clocks:
            commands.append(AgentCommand("give_clocks", []))
        return self._issue_commands(commands) if commands else []

    @staticmethod
    def _fill(text: str | None, details: dict[str, Any]) -> str | None:
        """Substitute {placeholders} in a scripted line from the beat details (unknown keys kept)."""
        if not text:
            return text
        return text.format_map(_SafeFormat(details))

    @staticmethod
    def _resolve_fly_to(
        fly_to: tuple[str, ...] | None,
        details: dict[str, Any],
    ) -> list[str] | None:
        """Resolve a beat's fly_to, substituting ("flower", "@id") from details.flower_id."""
        if not fly_to:
            return None
        args = list(fly_to)
        if args[:1] == ["flower"] and len(args) == 2 and args[1] == "@id":
            flower_id = details.get("flower_id")
            if flower_id is None:
                return None
            return ["flower", str(int(flower_id))]
        return args

    def _record_snapshot(self, snapshot: dict[str, Any]) -> None:
        game_tick = snapshot.get("game_tick")
        if not isinstance(game_tick, int):
            raise ValueError("agent_tick.snapshot.game_tick must be an integer")
        current_diversity = snapshot.get("current_diversity")
        if (
            not isinstance(current_diversity, int | float)
            or isinstance(current_diversity, bool)
        ):
            raise ValueError(
                "agent_tick.snapshot.current_diversity must be a number"
            )
        game_events = snapshot.get("game_events")
        if not isinstance(game_events, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("event_type"), str)
            and isinstance(item.get("game_tick"), int)
            and isinstance(item.get("details"), dict)
            for item in game_events
        ):
            raise ValueError(
                "agent_tick.snapshot.game_events must be a list of game events"
            )

        normalized = dict(snapshot)
        normalized["received_at"] = time.time()
        self.snapshots.append(normalized)

        player = snapshot.get("player")
        if not isinstance(player, dict):
            raise ValueError("agent_tick.snapshot.player must be an object")
        position = self._parse_position(player.get("position"))
        if position is None:
            raise ValueError(
                "agent_tick.snapshot.player.position must contain three numbers"
            )

        now = time.monotonic()
        self.positions.append(PositionSample(now, game_tick, position))
        while (
            self.positions
            and now - self.positions[0].received_at > STATIONARY_HISTORY_SECONDS
        ):
            self.positions.popleft()

    def _reconcile_execution(self, execution: dict[str, Any]) -> list[str]:
        current = self._optional_command_id(execution.get("current_command_id"))
        queued = self._command_id_list(
            execution.get("queued_command_ids"),
            "queued_command_ids",
        )
        completed = self._command_id_list(
            execution.get("completed_command_ids"),
            "completed_command_ids",
        )
        failed = self._command_id_list(
            execution.get("failed_command_ids"),
            "failed_command_ids",
        )

        for command_id in ([current] if current else []) + queued:
            tracked = self.commands.get(command_id)
            if tracked and tracked.state not in {"completed", "failed"}:
                tracked.state = "scheduled"
        for command_id in completed:
            tracked = self.commands.get(command_id)
            if tracked:
                tracked.state = "completed"
        for command_id in failed:
            tracked = self.commands.get(command_id)
            if tracked:
                tracked.state = "failed"
        return failed

    def _stationary_player_commands(self) -> list[dict[str, Any]]:
        stationary = self._is_stationary_for_window()
        if not stationary:
            self.stationary_alerted = False
            return []
        if self.stationary_alerted:
            return []

        self.stationary_alerted = True
        return self._issue_commands(
            [
                AgentCommand(
                    "say",
                    ["You've been still a while. Want to explore somewhere new?"],
                )
            ]
        )

    def _is_stationary_for_window(self) -> bool:
        if len(self.positions) < 2:
            return False
        samples = list(self.positions)
        if samples[-1].received_at - samples[0].received_at < STATIONARY_SECONDS:
            return False

        origin = samples[0].position
        return all(
            abs(sample.position[index] - origin[index]) <= STATIONARY_TOLERANCE
            for sample in samples
            for index in range(3)
        )

    def _generate(self, instructions: str, event: dict[str, Any]):
        """Call the provider, folding any pending scripted lines into the model's memory.

        The scripted lines are injected as prior assistant turns so the model sees what the player
        actually heard, then cleared (they are now part of the response chain). Also advances the
        response-chain pointer. On failure the pending lines are kept for the next attempt.
        """
        result = self.provider.generate(
            instructions=instructions,
            event=event,
            previous_response_id=self.previous_response_id,
            prior_assistant_lines=list(self.scripted_lines_pending) or None,
        )
        self.scripted_lines_pending.clear()
        self.previous_response_id = result.response_id
        return result

    def _record_scripted_line(self, text: str | None) -> None:
        """Remember a verbatim line Bip just spoke without the model, for the next call's memory."""
        if not text:
            return
        from beecurious_service.features import get_features

        if not get_features().scripted_memory:
            return
        self.scripted_lines_pending.append(text)
        if len(self.scripted_lines_pending) > MAX_SCRIPTED_MEMORY:
            del self.scripted_lines_pending[:-MAX_SCRIPTED_MEMORY]

    def _issue_commands(
        self,
        commands: list[AgentCommand],
    ) -> list[dict[str, Any]]:
        issued: list[dict[str, Any]] = []
        for command in commands:
            identified = command.issued()
            command_id = identified.command_id
            if command_id is None:
                raise AssertionError("issued command is missing command_id")
            self.commands[command_id] = TrackedCommand(identified)
            self.command_order.append(command_id)
            issued.append(identified.to_dict())
        self._trim_command_history()
        return issued

    def _outstanding_commands(
        self,
        execution: dict[str, Any],
    ) -> list[dict[str, Any]]:
        current = self._optional_command_id(execution.get("current_command_id"))
        queued = set(
            self._command_id_list(
                execution.get("queued_command_ids"),
                "queued_command_ids",
            )
        )
        acknowledged = queued | ({current} if current else set())
        return [
            self.commands[command_id].command.to_dict()
            for command_id in self.command_order
            if command_id in self.commands
            and self.commands[command_id].state == "issued"
            and command_id not in acknowledged
        ]

    def _trim_command_history(self) -> None:
        retained = set(self.command_order)
        for command_id in list(self.commands):
            if command_id not in retained:
                del self.commands[command_id]

    def _emit_commands(
        self,
        interaction_id: str | None,
        event_type: str,
        commands: list[dict[str, Any]],
        *,
        model: str | None,
        response_id: str | None = None,
        usage: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if not commands:
            return
        data: dict[str, Any] = {
            "event_type": event_type,
            "commands": commands,
            "model": model,
            "agent_profile": self.profile.profile_id,
        }
        if response_id:
            data["response_id"] = response_id
        if usage is not None:
            data["usage"] = usage
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        self._emit("agent_commands_generated", interaction_id, data)

    def _emit(
        self,
        event_type: str,
        interaction_id: str | None,
        data: dict[str, Any],
    ) -> None:
        if not self.telemetry_enabled:
            return
        self.telemetry.emit(
            TelemetryEvent(
                event_type=event_type,
                game_session_id=self.game_session_id,
                agent_session_id=self.agent_session_id,
                interaction_id=interaction_id,
                participant_id=self.participant_id,
                data=data,
            )
        )

    def retrieve_logs(
        self,
        *,
        event_type: str | None = None,
        interaction_id: str | None = None,
        hours: int = 24,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve Loki records correlated to this exact agent session."""
        return self.telemetry.retrieve_session_logs(
            self.agent_session_id,
            self.game_session_id,
            event_type=event_type,
            interaction_id=interaction_id,
            hours=hours,
            limit=limit,
        )

    @staticmethod
    def _parse_position(value: object) -> tuple[float, float, float] | None:
        if not isinstance(value, list) or len(value) != 3:
            return None
        if not all(isinstance(item, int | float) for item in value):
            return None
        return (float(value[0]), float(value[1]), float(value[2]))

    @staticmethod
    def _optional_command_id(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("current_command_id must be a string or null")
        return value or None

    @staticmethod
    def _command_id_list(value: object, name: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"{name} must be a list of strings")
        return list(dict.fromkeys(value))


class SessionStore:
    """Thread-safe in-memory store for active agent sessions."""
    def __init__(
        self,
        provider: AgentProvider,
        profile_registry: AgentProfileRegistry,
        default_agent_id: str,
        default_agent_version: str,
        telemetry: LokiTelemetry,
    ):
        self._provider = provider
        self._profile_registry = profile_registry
        self._default_agent_id = default_agent_id
        self._default_agent_version = default_agent_version
        self._telemetry = telemetry
        self._sessions: dict[str, AgentSession] = {}
        self._lock = Lock()

    def create(self, request: dict[str, Any] | None = None) -> AgentSession:
        """Resolve a profile, then create and retain a pinned agent session."""
        request = request or {}
        game_session_id = request.get("game_session_id")
        participant_id = request.get("participant_id")
        telemetry_enabled = request.get("logging_consent", False)
        requested_agent_id = request.get("agent", self._default_agent_id)
        requested_version = request.get("version", self._default_agent_version)
        if not isinstance(game_session_id, str) or not game_session_id.strip():
            raise ValueError("game_session_id must be a non-empty string")
        if participant_id is not None and not isinstance(participant_id, str):
            raise ValueError("participant_id must be a string")
        if not isinstance(telemetry_enabled, bool):
            raise ValueError("logging_consent must be a boolean")
        if not isinstance(requested_agent_id, str):
            raise ValueError("agent must be a string")
        if not isinstance(requested_version, str):
            raise ValueError("version must be a string")

        profile = self._profile_registry.resolve(
            requested_agent_id,
            requested_version,
        )
        session = AgentSession(
            agent_session_id=str(uuid4()),
            game_session_id=game_session_id.strip(),
            participant_id=participant_id.strip() if participant_id else None,
            telemetry_enabled=telemetry_enabled,
            profile=profile,
            provider=self._provider,
            telemetry=self._telemetry,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        session._emit(
            "agent_session_created",
            None,
            {"agent_profile": profile.profile_id},
        )
        return session

    def get(self, session_id: str) -> AgentSession | None:
        """Return an active session by identifier."""
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """Delete a session and report whether it existed."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session._emit(
            "agent_session_closed",
            None,
            {"agent_profile": session.profile.profile_id},
        )
        return True
