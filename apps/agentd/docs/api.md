# agentd REST API

OpenAI-compatible surface. JSON in / JSON out; SSE for streaming.

## Endpoints

### `GET /health`

```json
{ "status": "ok", "providers": 1 }
```

### `GET /v1/models`

```json
{
  "object": "list",
  "data": [
    {
      "id": "agentd/claude",
      "object": "model",
      "created": 1783795353,
      "owned_by": "agentd",
      "agentd": {
        "protocol": "stream-json",
        "status": "available",
        "label": "Claude Code"
      }
    }
  ]
}
```

OpenAI ignores unknown keys, so `agentd.*` is safe to round-trip through
existing OpenAI clients.

### `POST /v1/chat/completions`

OpenAI Chat Completions body schema (subset):

```json
{
  "model": "agentd/claude",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Reply with the single word PONG"}
  ],
  "stream": true,
  "tools": [],
  "tool_choice": null,
  "room_id": "openvox-voice-room-7",
  "session_id": null,
  "new_session": false
}
```

Extra fields recognised by agentd (all optional):

- `room_id` — reuses an existing session for this room if one exists.
- `session_id` — explicit agentd session id (returned by `/v1/sessions`).
- `new_session` — force a fresh session even if a room session exists.

Non-streaming response (`stream: false`):

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1783796000,
  "model": "agentd/claude",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "PONG"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 24, "completion_tokens": 4, "total_tokens": 28}
}
```

Streaming response (`stream: true`) — Server-Sent Events:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1783796000,"model":"agentd/claude","choices":[{"index":0,"delta":{"role":"assistant","content":"PONG"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1783796000,"model":"agentd/claude","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### `GET /v1/sessions`

```json
{
  "object": "list",
  "data": [
    {
      "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "object": "agentd.session",
      "provider": "claude",
      "room_id": "openvox-voice-room-7",
      "cli_session_id": "6ba073c2-b126-42d8-965f-154f3833c515",
      "created_at": "2026-07-11T18:49:49.647Z",
      "last_active_at": "2026-07-11T18:50:02.018Z"
    }
  ]
}
```

### `DELETE /v1/sessions/:id`

Terminate a session (kills the underlying CLI process if any).

```json
{ "id": "...", "object": "agentd.session", "closed": true }
```

## Status codes

| Code | When |
|---|---|
| 200 | normal response |
| 400 | body failed validation |
| 401 | bearer token missing (when `auth.tokens` is non-empty) |
| 403 | bearer token wrong |
| 404 | unknown model / unknown session id |
| 429 | rate limit exceeded |
| 503 | provider semaphore full |

## Calling from openvox (voice agent)

openvox is a Python voice-agent framework that lives at `~/workspace/openvox`.
This section sketches how to point it at agentd.

```python
# pseudocode
import httpx

async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {AGENTD_TOKEN}"},
        json={
            "model": "agentd/claude",
            "messages": history,
            "stream": True,
            "room_id": call_room_id,
        },
        timeout=None,
    )
    async for line in resp.aiter_lines():
        if line.startswith("data: "):
            payload = line.removeprefix("data: ")
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            text = chunk["choices"][0]["delta"].get("content", "")
            if text:
                await tts.stream(text)
```

Notes:

- Reuse the same `room_id` across turns in a single call to keep the
  underlying CLI session alive.
- Persist the `session_id` from `/v1/sessions` if you want to explicitly
  resume (e.g. after a restart).
- Use `new_session: true` to deliberately start a fresh context.
