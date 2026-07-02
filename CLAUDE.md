# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LiveKit Agents worker wired to **Volcengine (火山引擎)** voice services. The Python entrypoint in `main.py` builds a `livekit-agents` worker and registers a Volcengine-backed `AgentSession` (Realtime E2E model OR separate STT/LLM/TTS pipeline — toggle via `PIPELINE` env). The worker runs as either a local console, `dev` mode dispatcher, or `start` mode LiveKit Cloud worker.

## Common Commands

| Action | Command |
|--------|---------|
| Activate venv | `source .venv/bin/activate` |
| Run worker in LiveKit Cloud mode | `python main.py start` |
| Run worker in dev mode | `python main.py dev` |
| Test against a local room | `python main.py console` |
| Switch pipeline variant | `PIPELINE=pipeline python main.py start` (default: `realtime`) |
| Verify Volcengine connectivity | `python verify_volcengine.py` (saves `tts_sample.mp3` as audio proof) |
| Smoke test plugin imports | `python -c "from livekit.plugins import volcengine; print(volcengine.__all__)"` |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                          main.py                              │
│                                                              │
│  ┌────────────┐   ┌─────────────────────────────────────┐    │
│  │ Volcengine │   │           AgentSession              │    │
│  │   Agent    │ ◀─┤ (instructions, on_enter greeting)   │    │
│  └────────────┘   └─────────────────────────────────────┘    │
│         ▲                          ▲                          │
│         │                          │ picks one:               │
│         │                          ├─ realtime ─────────────┐│
│         │                          └─ pipeline (STT+LLM+TTS)││
└─────────┼─────────────────────────────────────────────────────┘
          │
   entrypoint(ctx)
          │
          ▼
   ┌──────────────┐    ┌──────────────────┐
   │ JobContext   │───▶│ LiveKit Room     │
   │ (real room   │    │ (real participants, WebRTC)
   │  dispatch)   │    │                  │
   └──────────────┘    └──────────────────┘
```

The dependency pinning matters: `livekit-plugins-volcengine` (from the
`di-osc/livekit-plugins-chinese` fork) currently declares
`livekit-agents==1.5.4`, but the installed transitive resolution lands
on `livekit-agents 1.2.9`. Use the new plugin API (`app_id`,
`access_token`, `model_name`, `ResourceId` defaults) — not the old
`cluster=` style that the PyPI 1.3.0 release uses.

## Key Files

- **`main.py`** — entrypoint, `VolcengineAgent`, `_build_session`,
  `_prewarm`. The two session variants are toggled by
  `os.environ.get("PIPELINE", "realtime")`.
- **`.env`** — Volcengine credentials (`*_APP_ID`, `*_ACCESS_TOKEN`,
  `VOLCENGINE_LLM_API_KEY`) and LiveKit placeholders.
- **`vendor/volcengine-src/livekit-plugins/livekit-plugins-volcengine/`** —
  editable install of the forked plugin sources. Update here and
  `pip install -e ...` to refresh.
- **`.venv/`** — Python 3.11 venv (`livekit-plugins-volcengine` is
  pinned to `>=3.10`).

## Plugin Behavior

| Component | Volcengine plugin class | Env var prefix | Positional kwarg |
|-----------|------------------------|----------------|------------------|
| Realtime E2E voice | `volcengine.RealtimeModel(bot_name=..., model="O")` | `VOLCENGINE_REALTIME_*` | `app_id`, `access_token` (keyword) |
| STT (streaming ASR) | `volcengine.STT(...)` | `VOLCENGINE_STT_*` | `app_id`, `access_token` (keyword) |
| TTS (豆包 V3 HTTP Chunked) | `volcengine.TTS(...)` | `VOLCENGINE_TTS_*` | `app_id` (positional) + `access_token` (keyword) |
| LLM (豆包 1.5-pro OpenAI-compatible) | `volcengine.LLM(model=..., api_key=...)` | `VOLCENGINE_LLM_API_KEY` | keyword-only |

`RealtimeModel.model` accepts `"O"` (standard features, web search,
premium voices) or `"SC"` (character-enhanced, cloned voices,
`character_manifest`).

## Known Gotchas

- **`prewarm_fnc` is called with the supervised process as a positional
  arg**, and the IPC layer pickles it across spawned workers. Use a
  module-level function (`def _prewarm(proc)`); a `lambda` raises
  `PicklingError`.
- The **editable install points at `vendor/volcengine-src/...`**,
  not the PyPI version. The PyPI 1.3.0 has a different API (`cluster`,
  old protocol) and will pull in conflicting deps if installed
  alongside the fork.
- **`LIVEKIT_API_KEY` is required even for `start`**, otherwise the
  worker fails at boot with `ValueError`. `.env` ships with
  `devplaceholder*` so the worker reaches the plugin load step; replace
  with real credentials from <https://cloud.livekit.io>.
- Worker binds port **8081** for IPC; a previous failed run can leave
  it occupied, causing the next start to fail with
  `OSError: [Errno 48] address already in use`. Fix with
  `lsof -ti:8081 | xargs kill -9`.

## Environment Setup

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ./vendor/volcengine-src/livekit-plugins/livekit-plugins-volcengine --no-deps
pip install "livekit-agents[otel,silero,turn-detector]~=1.5" python-dotenv
```

## Connectivity Evidence

`verify_volcengine.py` hits each Volcengine endpoint over the real network
and reports what came back. Last clean run on the supplied `.env`:

```
[1/4] LLM   → POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
  ✓ HTTP 200 reply='你好呀，希望能陪你度过愉快的时光！' tokens=40
[2/4] TTS   → POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
  ✓ HTTP 200 received 8 audio chunks, 21357 bytes of mp3 (saved to tts_sample.mp3)
[3/4] RT    → WS  wss://openspeech.bytedance.com/api/v3/realtime/dialogue
  ✓ WS handshake OK — server ack 72 bytes (auth + protocol confirmed)
[4/4] STT   → WS  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
  ⚠ WS handshake refused (server reachable; service not enabled for this app): 403
```

`file tts_sample.mp3` confirms the TTS response is real MPEG ADTS audio,
not a placeholder. STT 403 is server-side authorization for the supplied
AppID (not enabled for streaming big-model ASR in the console) — change
`PIPELINE=realtime` or activate ASR for the realtime E2E pipeline to work.

## References

- Plugin source: <https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine>
- Official agents repo: <https://github.com/livekit/agents>
- Upstream example: <https://github.com/livekit/agents/blob/main/examples/voice_agents/basic_agent.py>
