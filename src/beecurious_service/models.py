from dataclasses import dataclass
from typing import Any
from uuid import uuid4


ALLOWED_COMMANDS = {"say", "fly_to"}


@dataclass(frozen=True)
class AgentCommand:
    """A validated command that Fabric can execute in Minecraft."""
    type: str
    args: list[str]
    command_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the command in the JSON wire format."""
        command = {"type": self.type, "args": self.args}
        if self.command_id:
            command["command_id"] = self.command_id
        return command

    def issued(self) -> "AgentCommand":
        """Return this command with a service-generated execution identifier."""
        return AgentCommand(self.type, self.args, self.command_id or str(uuid4()))


def validate_commands(raw_commands: Any) -> list[AgentCommand]:
    """Validate model output and return supported agent commands."""
    if not isinstance(raw_commands, list):
        raise ValueError("commands must be a list")

    commands: list[AgentCommand] = []
    for raw_command in raw_commands:
        if not isinstance(raw_command, dict):
            continue

        command_type = raw_command.get("type")
        args = raw_command.get("args")
        if command_type not in ALLOWED_COMMANDS or not isinstance(args, list):
            continue

        string_args = [str(arg) for arg in args]
        if command_type == "say" and len(string_args) == 1 and string_args[0].strip():
            commands.append(AgentCommand(command_type, string_args))
        elif command_type == "fly_to" and _valid_fly_to_args(string_args):
            commands.append(AgentCommand(command_type, string_args))

    if not commands:
        raise ValueError("response did not contain a valid command")
    return commands


def _valid_fly_to_args(args: list[str]) -> bool:
    if args in (["player"], ["beehive"]):
        return True
    return len(args) == 2 and args[0] == "flower" and args[1].isdigit()
