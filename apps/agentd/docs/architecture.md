# agentd architecture

This document explains how agentd boots, finds providers, accepts work,
and terminates. Read alongside `src/daemon.ts` (the boot orchestrator).

## Boot sequence

```
┌────────────────┐
│ loadConfig()   │   ~/.agentd/config.json, zod-validated, defaults merged
└──────┬─────────┘
       │
       ▼
┌────────────────────────┐
│ discoverProviders()    │   walks PATH + ~/.local/bin, runs <bin> --version,
│                        │   caches at ~/.agentd/state.json
└──────┬─────────────────┘
       │
       ▼
┌────────────────────────┐
│ new ProviderRegistry() │   merges discovered binaries with
│   .registerCustom(...) │   config-defined providers
│   .load(discovered)    │
└──────┬─────────────────┘
       │
       ▼
┌────────────────────────┐
│ buildServer({cfg,registry,sessions})
│ Fastify + @fastify/rate-limit + bearer auth
└──────┬─────────────────┘
       │
       ▼
┌────────────────────────┐
│ TtlSweeper.start()     │   fires every ttl/4 seconds, closes idle sessions
└────────────────────────┘
```

## Request lifecycle: `POST /v1/chat/completions`

```
client
  │  POST /v1/chat/completions
  ▼
Fastify onRequest hook ── auth: 401 / 403 / pass
  │
  ▼
chatRoute handler
  │  resolve model `agentd/<provider>` → ProviderEntry
  │  resolve session (explicit id → room → new)
  │  acquire semaphore slot (per-provider)
  ▼
Provider.send({messages, resumeCliSessionId?, signal})
  │  spawn <bin> --output-format stream-json ── (or http, or acp)
  │  pump NDJSON → mapEventToChunk → SSE
  ▼
writeSseStream(reply, sseSource)  ──  data: {...}\n\n  stream ends with [DONE]
```

### Session id map (three tiers)

```
room_id  (caller-supplied, OpenVox room)
   ↕  1:N (most recent agentd wins)
agentd_session_id  (UUID v4, internal)
   ↕  1:1
cli_session_id  (CLI-native, e.g. Claude Code session_id)
```

Resume happens at the *CLI layer*, not by replaying message history — we
call `claude --resume <cliSessionId>` so the underlying CLI restores its
own context window.

### Subsystems

| Subsystem | File | Purpose |
|---|---|---|
| Config | `src/config/*` | zod-validated, defaults merged |
| Discovery | `src/providers/discovery.ts` | PATH walk + `--version` probe + cache |
| Registry | `src/providers/registry.ts` | `FACTORIES` factory table + merge |
| Providers | `src/providers/{claude,codex,openclaw,generic-acp,base}.ts` | per-protocol adapters |
| Sessions | `src/sessions/{manager,id-map,ttl}.ts` | lifecycle + id map + idle eviction |
| Concurrency | `src/util/semaphore.ts` | counting semaphore + per-key table |
| Stream adapters | `src/stream/{ndjson,openai-shape,sse}.ts` | protocol translators |
| Auth + rate limit | `src/api/server.ts`, `src/api/middleware/auth.ts` | bearer + per-key quota |
| Routes | `src/api/routes/{chat,models,sessions}.ts` | REST handlers |

## Provider protocols

| Protocol | Transport | Examples |
|---|---|---|
| `stream-json` | stdio NDJSON | Claude Code (`claude -p --output-format stream-json`) |
| `openai-http` | HTTP | OpenClaw (custom baseUrl) |
| `acp` | stdio JSON-RPC | generic ACP-compatible CLI |
| `jsonrpc` | stdio JSON-RPC | Codex (`codex app-server`) — best-effort |

To add a brand-new factory, see [adding-providers.md](adding-providers.md).

## Why pino + Fastify

Fastify ships its own pino integration — using it gets request-scoped child
loggers, ISO timestamps, and zero ceremony. JSON logs are easy to `jq` in
production (`AGENTD_LOG_LEVEL=info pnpm dev | jq`).

## Failure modes & degradation

- **CLI missing** — provider is omitted from `/v1/models`; the daemon stays up.
- **CLI OAuth missing** — provider is included with `agentd.status: "degraded"`
  in `/v1/models`; `/v1/chat` will probably 4xx from the CLI side. One warning
  log line is emitted.
- **CLI crashes mid-stream** — `Provider.send` catches the exit, emits a
  `ProviderEvent { type: 'error', fatal: true }`, then `{ type: 'done' }` so
  SSE always terminates cleanly.
- **Process leak on session close** — sessions own their `AbortController`;
  `DELETE /v1/sessions/:id` aborts, child processes receive SIGTERM.
