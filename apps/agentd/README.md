# agentd

A local self-hosted agent daemon that bridges ACP-compatible coding CLIs
(Claude Code, Codex, OpenClaw, or any ACP JSON-RPC stdio binary) to an
**OpenAI-compatible REST API**.

Built for personal/dev use. Run it on your own machine; point your favourite
chat client (or openvox voice agent) at `http://127.0.0.1:8787/v1`.

---

## TL;DR

```bash
pnpm install
pnpm build
pnpm dev                # listens on 8787
curl http://127.0.0.1:8787/v1/models
```

Stream a chat completion:

```bash
curl -N -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "agentd/claude",
    "messages": [{"role":"user","content":"Reply with the single word PONG"}],
    "stream": true
  }'
```

Run the acceptance script (probes + tests):

```bash
AGENTD_AUTO_START=1 ./scripts/verify.sh
```

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET    | `/health`                   | liveness + provider count |
| GET    | `/v1/models`                | list providers (id format `agentd/<provider>`) |
| POST   | `/v1/chat/completions`      | OpenAI Chat Completions, supports `stream: true` SSE |
| GET    | `/v1/sessions`              | list active agentd sessions |
| DELETE | `/v1/sessions/:id`          | terminate a session |
| GET    | `/healthz`                  | alias for `/health` |

Body schema for `/v1/chat/completions` follows OpenAI exactly: `model`, `messages`,
`stream`, `tools`, `tool_choice`. The `model` field routes to a specific provider
(`agentd/claude`, `agentd/codex`, `agentd/openclaw`, etc.).

---

## Configuration

The daemon reads `~/.agentd/config.json` on every start. Missing files
auto-create with defaults.

```jsonc
{
  "port": 8787,
  "host": "127.0.0.1",
  "sessionTtlSeconds": 1800,
  "maxConcurrentPerProvider": 4,
  "rateLimit": { "max": 60, "windowMs": 60000 },
  "auth": { "tokens": ["secret-1"] },
  "providers": [
    {
      "id": "my-agent",
      "label": "Custom ACP Agent",
      "command": "/usr/local/bin/my-acp-cli",
      "args": ["--serve"],
      "protocol": "acp"
    }
  ]
}
```

Anything not provided falls back to defaults via zod-parsed dot-path merge.
Use `AGENTD_PORT` / `AGENTD_HOST` env vars to override the listen address per
run.

---

## Auth

If `auth.tokens` is non-empty, every request (except `/health*`) requires a
`Authorization: Bearer <token>` header. The token list is shared-secret style
— sufficient for local-loopback use; not a substitute for a real auth proxy
in a multi-user deployment.

The rate limiter keys on the bearer token when present, falling back to IP.
`@fastify/rate-limit` enforces `rateLimit.max` requests per `rateLimit.windowMs`.

---

## Add a Provider

See [docs/adding-providers.md](docs/adding-providers.md). The short version:

1. Detect at startup: `src/providers/discovery.ts` walks PATH for `claude`,
   `codex`, `openclaw`.
2. Extend `FACTORIES` in `src/providers/registry.ts` with a new factory.
3. Optionally add a `providers[]` entry to `~/.agentd/config.json` for fully
   custom binaries (no auto-discovery needed).

The `generic-acp` factory is the escape hatch — wrap any ACP-compatible
stdio binary without writing new code:

```json
{
  "providers": [
    {
      "id": "my-acp",
      "label": "My ACP CLI",
      "command": "/path/to/my-cli",
      "args": ["--stdio"],
      "protocol": "acp"
    }
  ]
}
```

---

## Architecture

See [docs/architecture.md](docs/architecture.md). Short tour:

- **`src/daemon.ts`** — boot sequence (config → discovery → registry → server).
- **`src/api/server.ts`** — Fastify instance with bearer auth + rate limit.
- **`src/api/routes/{chat,models,sessions}.ts`** — REST handlers.
- **`src/providers/*`** — one file per provider; each spawns its own
  subprocess and emits `ProviderEvent` stream.
- **`src/sessions/{manager,id-map,ttl}.ts`** — three-tier session id map
  (`room_id ↔ agentd_session_id ↔ cli_session_id`) + idle-TTL sweeper.
- **`src/stream/{ndjson,openai-shape,sse}.ts`** — protocol adapters.
- **`src/util/semaphore.ts`** — per-provider concurrency cap.

Reference code: `~/workspace/paseo/packages/server/src/server/agent/` for the
factory table pattern (`provider-registry.ts:109`) and Claude Code spawn
(`providers/claude/agent.ts`).

---

## Development

```bash
pnpm dev          # tsx watch — auto-reload on file changes
pnpm test         # vitest
pnpm build        # tsc → dist/
pnpm start        # node dist/index.js (production-style)
pnpm typecheck    # tsc --noEmit
```

Status persistence:
- `~/.agentd/config.json` — user config (auto-created on first run)
- `~/.agentd/state.json` — discovered binaries cache
- `~/.agentd/sessions.json` — active sessions, replay on restart

Logs: pino structured JSON to stdout. Set `AGENTD_LOG_LEVEL=debug` for
chattier output, `AGENTD_LOG_LEVEL=warn` for quieter.

---

## Out of Scope

- ❌ Business auth/quota/billing for OpenAI compatibility — agentd only
  proxies local CLIs.
- ❌ Web UI.
- ❌ Docker/k8s deployment.
- ❌ Windows support.

This is intentional: agentd is a thin local proxy, not a hosted service.
