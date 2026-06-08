# BeeCuriousService

Python service that owns Bip's prompt, conversation state, model calls, and command generation.
The Fabric mod sends game events and world context to this service, then executes the returned
Minecraft commands.

## Run locally

The default provider is deterministic and does not require an API key:

```bash
PYTHONPATH=src python3 -m beecurious_service
```

The service listens on `http://127.0.0.1:8765`.
After installing the project, the `beecurious-service` command is also available.
Configuration is loaded from `BeeCuriousService/.env` when present. Service output is written
to both the terminal and `logs/beecurious-service.log`.

## OpenAI provider

```bash
export BEECURIOUS_AGENT_PROVIDER=openai
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
export BEECURIOUS_MODEL=gpt-5.2
PYTHONPATH=src python3 -m beecurious_service
```

Optional OpenAI settings are `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`.
Install the project dependencies first with `python3 -m pip install -e .`. HTTPS verification
uses the `certifi` CA bundle. A managed network can override it with `SSL_CERT_FILE`.

## API

- `GET /health`
- `POST /v1/sessions`
- `POST /v1/sessions/{session_id}/events`
- `DELETE /v1/sessions/{session_id}`

Run tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
