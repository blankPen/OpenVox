---
type: Runbook
title: OpenVox Local Runbook
description: Operator-facing guide to starting, dispatching, and troubleshooting the OpenVox worker locally.
tags: [operations, runbook, troubleshooting, livekit]
---

# Local Runbook

This page is the operator-facing counterpart to the architecture pages. It assumes `~/.openvox/config.json` already exists (see [Configuration → Config loader](../configuration/config-loader.md) for the schema) and that the developer venv at `.venv/bin/python` exists.

## Three-terminal start

```mermaid
flowchart TD
    A[Terminal A: LiveKit server] --> B[Terminal B: OpenVox worker]
    B --> C[Terminal C: dispatch + client]
    A -- docker start voice-assistant-livekit-1 --> A
    B -- ./scripts/start.sh --> B2[python main.py start in background]
    C -- lk dispatch create --room demo --agent-name openz --> Server[LiveKit routes job to worker]
    Server --> B
```

### Terminal A — LiveKit server

A Docker container (`voice-assistant-livekit-1`) is already expected to be running on the macOS dev machine. If it is not:

```bash
docker run -d --name local-livekit --restart=always \
  -p 7880-7882:7880-7882 -p 7882:7882/udp \
  livekit/livekit-server:latest --dev
```

The `--dev` flag hard-codes `devkey` / `secret` as the API key/secret. If your `~/.openvox/config.json` uses a non-`devkey`/`secret` pair, the worker handshake will 401.

### Terminal B — OpenVox worker

```bash
cd <repo-root>
source .venv/bin/activate
./scripts/start.sh           # background, logs to /tmp/livekit-worker.log
# or:
./scripts/start.sh fg        # foreground (Ctrl-C to stop)
# or:
./scripts/start.sh status    # last 15 log lines + pid
./scripts/start.sh stop      # kill any process on port 8081
```

Internally `scripts/start.sh`:

1. Resolves `PY` from `.venv/bin/python`, falling back to `python3` / `python`.
2. Verifies `$OPENVOX_CONFIG` (default `~/.openvox/config.json`) exists and parses as JSON.
3. Exports `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` from the `livekit.*` section so the LiveKit SDK's `os.environ` lookup succeeds.
4. In `start` mode, runs `lsof -ti:8081 | xargs kill -9` (stale worker IPC port), then launches `python main.py start > $LOG 2>&1 &` and waits 5s to confirm the port is open.

### Terminal C — dispatch + client

```bash
# Send the agent to "demo" room
lk dispatch create --dev --room demo --agent-name openz

# Generate a join token for a client identity
lk token create --dev --room demo --identity alice --join

# Option 1 — terminal test, zero dependencies
lk room join demo --identity alice --dev \
  --publish hello.ogg --auto-subscribe --exit-after-publish

# Option 2 — browser
ngrok http 7880                                # expose wss URL
# paste the wss URL into https://meet.livekit.io/custom

# Option 3 — local React playground
git clone https://github.com/livekit/agents-playground
# set LIVEKIT_URL/API_KEY/SECRET in its .env, run
```

The dispatch agent name **must** match `livekit.agent_name` in the config (currently `openz`); see [Configuration → Config loader](../configuration/config-loader.md).

## Direct commands (without scripts)

```bash
# Foreground dev mode (interactive)
python main.py dev

# Console mode (terminal <-> agent chat)
python main.py console

# Smoke-test that the volcengine plugin imports cleanly
python -c "from livekit.plugins import volcengine; print(volcengine.__all__)"

# After changing the plugin source, re-install editable
pip install -e ./plugins/livekit-plugins-volcengine --no-deps
```

## IPC port 8081

The worker spawns one child process per dispatched job and uses port `8081` as the IPC channel between the supervisor and job workers. Crashed jobs sometimes leave the port held, in which case the next `start` reports:

```
OSError: [Errno 48] address already in use
```

`scripts/start.sh` handles this automatically. If you bypass the script, run:

```bash
lsof -ti:8081 | xargs kill -9
```

`docker-compose.yml` uses the default `bridge` network rather than `host` specifically to keep `8081` off the host (a `host` network would let multiple workers collide).

## LiveKit credential mismatch (401 handshake)

The LiveKit server's `--dev` mode hard-codes `devkey` / `secret`. If `~/.openvox/config.json` carries a different pair (e.g. `openz` / `openz-secret` from `livekit.yaml`), the JWT the worker signs will be rejected:

```
WSServerHandshakeError 401
```

Two options:

- Run a bare `livekit-local` container with `--dev --bind=0.0.0.0` and set the config creds to `devkey` / `secret`.
- Mount `livekit.yaml`, drop `--dev`, and align `.env` / config with the server's keypair. The repo's `CLAUDE.md` references `start-lan.sh` and `start-emu.sh` for these modes (those scripts are not in the current worktree — see [Quickstart → Backlog](../quickstart.md)).

## Troubleshooting table

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ValueError: api_key is required` at worker boot | `volcengine.*.app_id` / `access_token` missing from config | `cat ~/.openvox/config.json`; fill in `volcengine.stt.*` and `volcengine.tts.*`. |
| `PicklingError: Can't pickle <lambda>` | `prewarm_fnc` is a lambda | Use a module-level function with signature `def _prewarm(proc): ...`. The default in `main.py` already does this. |
| `prewarm_fnc() takes 0 positional arguments but 1 was given` | `prewarm_fnc` signature is missing the `proc` arg | Add `proc` as the only positional parameter. |
| `OSError: address already in use` on port 8081 | previous worker not cleaned up | `lsof -ti:8081 \| xargs kill -9` then `./scripts/start.sh`. |
| `WSServerHandshakeError 401` | API key/secret mismatch with LiveKit server | Make `.env` / config creds match what the server uses (`devkey` / `secret` in `--dev`). |
| `lk dispatch create: agent-name is required` | worker has no `agent_name` set | `livekit.agent_name` missing from config. |
| `lk token create: failed to fetch` | LiveKit server not reachable on `LIVEKIT_URL` | `curl http://localhost:7880/` should return 200; check the container. |
| Hermes api_server responds `400 No user message found in messages` on the very first reply | `generate_reply()` was called without `user_input` | Already fixed in `main.py` (`on_enter` passes `user_input="打招呼"`); see [Integrations → Hermes LLM](../integrations/hermes-llm.md). |
| Worker log shows `exception was never retrieved` on disconnect | STT `recv_task` raised `CancelledError` into a `_GatheringFuture` nobody awaits | Already fixed by `_patched_stt_run` in `main.py` (top of file). |
| STT 403 from Volcengine | AppID does not have "流式语音识别 大模型" service enabled in Volcengine console | Enable the service at <https://console.volcengine.com/voice/app>. |
| Tests fail with `Address already in use` for `livekit_server` (only in e2e) | another test or worker is bound | Stop any running worker (`./scripts/start.sh stop`) and any other LiveKit server. |

## Tests

`scripts/run_tests.sh` accepts `unit`, `e2e`, or `full` (default `unit`):

```bash
./scripts/run_tests.sh unit   # all tests/, no LiveKit required
./scripts/run_tests.sh e2e    # tests/e2e_pipeline.py only (needs LiveKit server + worker)
./scripts/run_tests.sh full   # both
```

The `e2e` mode requires `.env` (which is **not** used by the worker itself — only by the test bootstrap in `tests/e2e_pipeline.py` to set `LIVEKIT_*` for `lk` CLI calls and the LiveKit Python SDK). See [Testing → Overview](../testing/overview.md).

## Source anchors

- `README.md` (Chinese operator manual; the source of most of the above table)
- `CLAUDE.md` lines 26–69 (concise command reference + pitfalls)
- `scripts/start.sh` lines 11–53, 59–123
- `scripts/run_tests.sh` lines 30–73
- `pyproject.toml` `[tool.pytest.ini_options]` (pythonpath = ["."])
- `.gitignore` lines 8–27 (excludes `workspace/users/*`, `workspace/sandbox/*`, `workspace/extensions/mcp/*.local.json` — runtime-only directories not present in this worktree)