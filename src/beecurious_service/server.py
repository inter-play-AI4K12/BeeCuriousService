from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from beecurious_service.agents.registry import create_agent_registry
from beecurious_service.config import Settings, load_dotenv
from beecurious_service.providers import create_profile_providers, create_provider
from beecurious_service.sessions import SessionStore
from beecurious_service.telemetry import LokiNotConfiguredError, LokiTelemetry


LOG = logging.getLogger(__name__)


class BeeCuriousRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for health checks and agent session requests."""
    store: SessionStore

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        session_id = self._logs_session_id(path)
        if session_id:
            session = self.store.get(session_id)
            if session is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                return
            try:
                filters = self._log_filters(parse_qs(parsed_url.query))
                records = session.retrieve_logs(**filters)
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "agent_session_id": session.agent_session_id,
                        "game_session_id": session.game_session_id,
                        "count": len(records),
                        "records": records,
                    },
                )
            except ValueError as exc:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except LokiNotConfiguredError as exc:
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc)},
                )
            except (OSError, TimeoutError) as exc:
                LOG.warning("Loki query failed for session %s: %s", session_id, exc)
                self._write_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": "loki_query_failed"},
                )
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/v1/sessions":
                session = self.store.create(payload)
                LOG.info(
                    "Created session %s with profile=%s",
                    session.session_id,
                    session.profile.profile_id,
                )
                self._write_json(
                    HTTPStatus.CREATED,
                    {
                        "agent_session_id": session.agent_session_id,
                        "agent": session.profile.agent_id,
                        "version": session.profile.version,
                        "agent_name": session.profile.display_name,
                    },
                )
                return

            session_id = self._event_session_id(path)
            if session_id:
                session = self.store.get(session_id)
                if session is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "session_not_found"})
                    return
                self._write_json(HTTPStatus.OK, session.handle_event(payload))
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (json.JSONDecodeError, ValueError) as exc:
            LOG.warning("Rejected POST %s: %s", path, exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            LOG.exception("Request failed")
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "agent_request_failed"},
            )

    def do_DELETE(self) -> None:
        path_parts = urlparse(self.path).path.strip("/").split("/")
        if len(path_parts) == 3 and path_parts[:2] == ["v1", "sessions"]:
            deleted = self.store.delete(path_parts[2])
            status = HTTPStatus.NO_CONTENT if deleted else HTTPStatus.NOT_FOUND
            self.send_response(status)
            self.end_headers()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def log_message(self, format_string: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), format_string % args)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _event_session_id(path: str) -> str | None:
        path_parts = path.strip("/").split("/")
        if len(path_parts) == 4 and path_parts[:2] == ["v1", "sessions"]:
            if path_parts[3] == "events":
                return path_parts[2]
        return None

    @staticmethod
    def _logs_session_id(path: str) -> str | None:
        path_parts = path.strip("/").split("/")
        if len(path_parts) == 4 and path_parts[:2] == ["v1", "sessions"]:
            if path_parts[3] == "logs":
                return path_parts[2]
        return None

    @staticmethod
    def _log_filters(query: dict[str, list[str]]) -> dict[str, Any]:
        event_type = BeeCuriousRequestHandler._optional_filter(query, "event_type")
        interaction_id = BeeCuriousRequestHandler._optional_filter(
            query,
            "interaction_id",
        )
        return {
            "event_type": event_type.lower() if event_type else None,
            "interaction_id": interaction_id,
            "hours": BeeCuriousRequestHandler._bounded_query_int(
                query,
                "hours",
                24,
                1,
                24 * 30,
            ),
            "limit": BeeCuriousRequestHandler._bounded_query_int(
                query,
                "limit",
                100,
                1,
                1000,
            ),
        }

    @staticmethod
    def _optional_filter(
        query: dict[str, list[str]],
        name: str,
    ) -> str | None:
        values = query.get(name)
        if not values:
            return None
        value = values[0].strip()
        return value or None

    @staticmethod
    def _bounded_query_int(
        query: dict[str, list[str]],
        name: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        values = query.get(name)
        if not values:
            return default
        try:
            value = int(values[0])
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < minimum or value > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value


def create_server(settings: Settings) -> ThreadingHTTPServer:
    """Create a configured BeeCurious HTTP server."""
    provider = create_provider(settings)
    profile_providers = create_profile_providers(settings)
    profile_registry = create_agent_registry()
    telemetry = LokiTelemetry(
        settings.loki_url,
        settings.loki_username,
        settings.loki_password,
    )
    BeeCuriousRequestHandler.store = SessionStore(
        provider,
        profile_registry,
        settings.default_agent_id,
        settings.default_agent_version,
        telemetry,
        profile_providers,
    )
    return ThreadingHTTPServer((settings.host, settings.port), BeeCuriousRequestHandler)


def main() -> None:
    """Run BeeCuriousService until interrupted."""
    dotenv_path = load_dotenv()
    log_directory = Path.cwd() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "beecurious-service.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    settings = Settings.from_environment()
    server = create_server(settings)
    if dotenv_path:
        LOG.info("Loaded environment from %s", dotenv_path)
    else:
        LOG.info("No .env file found at %s; using process environment and defaults",
                 Path.cwd() / ".env")
    LOG.info("Service log: %s", log_path)
    LOG.info(
        "BeeCuriousService listening on http://%s:%s with provider=%s default_agent=%s@%s",
        settings.host,
        settings.port,
        settings.provider,
        settings.default_agent_id,
        settings.default_agent_version,
    )
    LOG.info(
        "Loki telemetry is %s",
        "enabled" if settings.loki_password else "disabled (LOKI_PASSWORD is not set)",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping BeeCuriousService")
    finally:
        server.server_close()
