from dataclasses import dataclass
import json
import os
import ssl
from typing import Any, Protocol
from urllib import error, request

import certifi

from beecurious_service.config import Settings
from beecurious_service.models import AgentCommand, validate_commands


@dataclass(frozen=True)
class ProviderResult:
    """Commands and provider state returned by an agent provider."""
    commands: list[AgentCommand]
    response_id: str | None = None
    usage: dict[str, Any] | None = None
    model: str | None = None


class AgentProvider(Protocol):
    """Interface implemented by agent command providers."""
    def generate(
        self,
        instructions: str,
        event: dict[str, Any],
        previous_response_id: str | None,
    ) -> ProviderResult:
        ...


class MockAgentProvider:
    """Deterministic provider for local development and tests."""
    def generate(
        self,
        instructions: str,
        event: dict[str, Any],
        previous_response_id: str | None,
    ) -> ProviderResult:
        del instructions, previous_response_id
        event_type = event.get("event_type")

        if event_type == "game_start":
            raw = [
                {"type": "say", "args": ["Hi! I'm Bip. What should we explore first?"]},
                {"type": "fly_to", "args": ["player"]},
            ]
        elif event_type == "game_end":
            raw = [{"type": "say", "args": ["The garden changed a lot. What pattern did you notice?"]}]
        else:
            raw = [{"type": "say", "args": ["I heard you. Let's look around together!"]}]

        return ProviderResult(validate_commands(raw), model="mock")


class OpenAIAgentProvider:
    """OpenAI Responses API provider with verified HTTPS."""
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        self._settings = settings
        ca_bundle = os.getenv("SSL_CERT_FILE") or certifi.where()
        self._ssl_context = ssl.create_default_context(cafile=ca_bundle)

    def generate(
        self,
        instructions: str,
        event: dict[str, Any],
        previous_response_id: str | None,
    ) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "instructions": instructions,
            "input": json.dumps(event),
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id

        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        if self._settings.openai_org_id:
            headers["OpenAI-Organization"] = self._settings.openai_org_id
        if self._settings.openai_project_id:
            headers["OpenAI-Project"] = self._settings.openai_project_id

        endpoint = self._settings.openai_base_url.rstrip("/") + "/responses"
        http_request = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(
                http_request,
                timeout=60,
                context=self._ssl_context,
            ) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI request failed ({exc.code}): {details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not connect securely to OpenAI: {exc.reason}") from exc

        output_text = _extract_output_text(response_body)
        parsed_output = json.loads(output_text)
        return ProviderResult(
            commands=validate_commands(parsed_output.get("commands")),
            response_id=response_body.get("id"),
            usage=response_body.get("usage")
            if isinstance(response_body.get("usage"), dict)
            else None,
            model=self._settings.model,
        )


class RochesterAgentProvider:
    """Provider for the Rochester argumentation conversation API."""

    def __init__(self, settings: Settings):
        if not settings.rochester_api_key:
            raise ValueError(
                "ROCHESTER_API_KEY is required for the Rochester provider"
            )
        self._settings = settings
        ca_bundle = os.getenv("SSL_CERT_FILE") or certifi.where()
        self._ssl_context = ssl.create_default_context(cafile=ca_bundle)

    def generate(
        self,
        instructions: str,
        event: dict[str, Any],
        previous_response_id: str | None,
    ) -> ProviderResult:
        event_json = json.dumps(event)
        if previous_response_id:
            endpoint = "/step"
            payload = {
                "model": self._settings.rochester_model,
                "message": f"Current Minecraft event JSON:\n{event_json}",
                "reference": previous_response_id,
            }
        else:
            endpoint = "/init"
            payload = {
                "model": self._settings.rochester_model,
                "story": instructions,
                "question": (
                    "How should Bip respond to this Minecraft event?\n"
                    f"{event_json}"
                ),
                "answer": (
                    "Follow the story instructions exactly and return only "
                    "the required JSON commands."
                ),
            }

        response_body = self._post(endpoint, payload)
        response_text = response_body.get("response")
        reference = response_body.get("reference")
        if not isinstance(response_text, str):
            raise ValueError("Rochester response did not contain response text")
        if not isinstance(reference, str):
            raise ValueError("Rochester response did not contain a reference")

        commands = _rochester_commands(response_text)
        return ProviderResult(
            commands=commands,
            response_id=reference,
            model=f"rochester:{self._settings.rochester_model}",
        )

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        http_request = request.Request(
            self._settings.rochester_base_url.rstrip("/") + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.rochester_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(
                http_request,
                timeout=60,
                context=self._ssl_context,
            ) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Rochester request failed ({exc.code}): {details}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"Could not connect securely to Rochester: {exc.reason}"
            ) from exc
        if not isinstance(response_body, dict):
            raise ValueError("Rochester response was not a JSON object")
        return response_body


def _extract_output_text(response_body: dict[str, Any]) -> str:
    if isinstance(response_body.get("output_text"), str):
        return response_body["output_text"]

    for output_item in response_body.get("output", []):
        for content_item in output_item.get("content", []):
            text = content_item.get("text")
            if isinstance(text, str):
                return text
    raise ValueError("OpenAI response did not contain output text")


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _rochester_commands(response_text: str) -> list[AgentCommand]:
    stripped = _strip_json_fence(response_text)
    try:
        parsed_output = json.loads(stripped)
    except json.JSONDecodeError:
        words = stripped.split()
        dialogue = " ".join(words[:15])
        if len(words) > 15:
            dialogue = dialogue.rstrip(".,!?;:") + "..."
        return validate_commands(
            [{"type": "say", "args": [dialogue]}]
        )
    if not isinstance(parsed_output, dict):
        raise ValueError("Rochester JSON response was not an object")
    return validate_commands(parsed_output.get("commands"))


def create_provider(settings: Settings) -> AgentProvider:
    """Create the configured agent provider."""
    if settings.provider == "mock":
        return MockAgentProvider()
    if settings.provider == "openai":
        return OpenAIAgentProvider(settings)
    raise ValueError(f"unsupported provider: {settings.provider}")


def create_profile_providers(settings: Settings) -> dict[str, AgentProvider]:
    """Create providers pinned by specific agent profiles when configured."""
    providers: dict[str, AgentProvider] = {}
    if settings.rochester_api_key:
        providers["rochester"] = RochesterAgentProvider(settings)
    return providers
