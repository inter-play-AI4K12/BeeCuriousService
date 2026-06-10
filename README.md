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

## Agent profiles

The service selects an immutable agent profile when a session is created. Configure the
Python-side default without rebuilding Fabric:

```dotenv
BEECURIOUS_DEFAULT_AGENT=bip
BEECURIOUS_DEFAULT_AGENT_VERSION=1.0
```

Fabric includes `game_session_id` and `logging_consent` when it creates a session. Tests or
experiments may also explicitly request a profile. Unknown profiles are rejected rather than
silently replaced. The session response and logs report the resolved profile.

New behavior versions should be added under `src/beecurious_service/agents/` and registered in
`agents/registry.py`. Existing study profiles should remain immutable.

Available profiles:

- `bip@1.0`: minimal friendly baseline with only the command contract
- `bip@2.0`: full spatially engaged Bip prompt
- `bip@3.0`: Bip 1.0 plus an in-memory stationary-player check; after one minute
  at the same heartbeat position, Bip suggests exploring

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

## Loki telemetry

Set `LOKI_PASSWORD` to enable asynchronous structured telemetry. `LOKI_URL` and `LOKI_USER`
default to the BeeTrap Loki deployment. Telemetry remains disabled when the session request has
`"logging_consent": false`, even if the service has Loki credentials.

Each record keeps `game_session_id` and `agent_session_id` separate. The session-creation response
uses the explicit `agent_session_id` field. Agent inputs, context, and
generated commands are JSON fields in the log body, not Loki labels. Do not put participant names
in `participant_id`; use the study's pseudonymous participant code.

## API

- `GET /health`
- `POST /v1/sessions` with `game_session_id`, `logging_consent`, and optional `participant_id`
- `POST /v1/sessions/{agent_session_id}/events`
- `GET /v1/sessions/{agent_session_id}/logs` with optional `event_type`,
  `interaction_id`, `hours`, and `limit` query parameters
- `DELETE /v1/sessions/{agent_session_id}`

The logs endpoint only accepts an active `agent_session_id`. Returned Loki records must match
both that agent session and its associated `game_session_id`, preventing the two identifiers from
being treated interchangeably.

Fabric sends `agent_tick` once per second with a compact world snapshot and command queue IDs.
Heartbeats update bounded in-memory session history and normally return no commands. Python assigns
each new command a `command_id`, retains its body, and resends only commands Fabric has not
acknowledged. Fabric logs each heartbeat to Loki as one `game_state` event containing the same
`snapshot` and `execution` objects sent to the agent service.

The snapshot includes `current_diversity` and a `game_events` array buffered since the previous
successful heartbeat. Fabric currently reports agent movement, agent-player collisions, player attacks
on the agent, pollination start/end, bud rankings, and beehive movement with event-specific details.
All Loki `event_type` values use lowercase `snake_case` across Fabric and Python.

Run tests with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
