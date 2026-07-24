---
type: Integration
title: Vendored livekit-plugins-volcengine
description: How OpenVox embeds and patches the Volcengine STT/TTS/LLM/Realtime plugin, and the kwarg contract.
tags: [volcengine, plugin, vendored]
---

# Vendored `livekit-plugins-volcengine`

OpenVox ships a vendored copy of [`di-osc/livekit-plugins-chinese`](https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine) under `plugins/livekit-plugins-volcengine/`. It is installed editable with `--no-deps` so that the project's own `pyproject.toml` pin (`livekit-agents[otel,silero,turn-detector]~=1.5`) is not downgraded to the plugin's hard pin (`livekit-agents==1.5.4`).

## Why vendored and not PyPI

- PyPI's `livekit-plugins-volcengine==1.3.0` uses positional / cluster parameters that conflict with this vendored copy. Installing both would lead to two `livekit.plugins.volcengine` modules being loaded depending on sys.path order.
- The vendored version uses keyword-only kwargs (`app_id=`, `access_token=`, `model=`, `api_key=`) that match what `main.py` calls; the PyPI version predates that.
- The vendored plugin is patched in two places (see below). Centralising it locally makes the patches obvious.

## Install

```bash
pip install -e ./plugins/livekit-plugins-volcengine --no-deps
```

The `--no-deps` is **mandatory**. The vendored `pyproject.toml` pins `livekit-agents==1.5.4`, and pip would otherwise downgrade the host venv to 1.5.4 and break the `[otel,silero,turn-detector]` extras required by `pyproject.toml` at the repo root.

## Surface used by OpenVox

`main.py` only uses three of the five exports in `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/__init__.py`:

| Class | Module | Constructor kwargs (in OpenVox) |
|-------|--------|----------------------------------|
| `STT` | `stt.py` | `app_id=`, `access_token=` |
| `TTS` | `tts.py` | `app_id=`, `access_token=` |
| `LLM` | `llm.py` | (not used by OpenVox — see [Integrations → Hermes LLM](./hermes-llm.md)) |
| `RealtimeModel` | `realtime.py` | (not used — `tests/test_main_build_session.py::test_volcengine_realtime_branch_removed` enforces absence) |

### `STT` — streaming speech recognition

`plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/stt.py` defines `STTOptions` with the protocol fields needed for Volcengine's WebSocket `/api/v3/sauc/bigmodel` interface:

- Default `base_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"`
- Default `format = "pcm"`, `sample_rate = 16000`, `bits = 16`, `num_channels = 1`, `language = "zh-CN"`
- `model_name = "bigmodel"`, `result_type = "single"`, `enable_punc = True`
- Optional: `enable_itn`, `enable_ddc`, `show_utterance`, `vad_segment_duration`, etc.

OpenVox passes **only** `app_id` + `access_token` from the config; everything else uses the defaults. See [Configuration → Config loader](../configuration/config-loader.md) for the `volcengine.stt.*` schema.

### `TTS` — chunked HTTP synthesis

`plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/tts.py` calls Volcengine's `/api/v3/tts/unidirectional` chunked HTTP endpoint:

- Default `base_url = "https://openspeech.bytedance.com"`
- Default `voice = "zh_female_xiaohe_uranus_bigtts"`, `resource_id = "seed-tts-2.0"`
- Default `sample_rate = 24000`, `encoding = "pcm"`, `speed/volume/pitch = 1.0`

The headers set on every request are:

```text
Content-Type:        application/json
X-Api-App-Id:        <app_id>
X-Api-App-Key:       <app_id>
X-Api-Access-Key:    <access_token>
X-Api-Resource-Id:   <resource_id>
X-Api-Request-Id:    <shortuuid>   (if reqid is given)
```

OpenVox passes **only** `app_id` + `access_token`. The `access_token` is also read from `os.environ["VOLCENGINE_TTS_ACCESS_TOKEN"]` as a fallback inside the plugin (vestigial — OpenVox never sets that env).

## Patches applied by `main.py`

Two runtime monkey-patches target the vendored STT class `livekit.plugins.volcengine.stt.SpeechStream`:

1. **`_process_stream_event` wrapper** — after the original method runs, the patch parses the response payload and logs `[用户语音] <text>` only when `utterances[0].definite` is `True`. This is the operator-visible marker for what the user said. The patch uses `parse_response` from the same module to decode the raw data dict.
2. **`_run` wrapper** — catches `asyncio.CancelledError` around `await _orig_stt_run(self)` and returns silently. The original `_run` starts a nested `recv_task` on `aiohttp.ClientSession.ws.receive()`; on shutdown `livekit-agents 1.6.x` cancels every task in the child process, and the `_GatheringFuture` exception bubbles out as "exception was never retrieved" stderr noise. The patch preserves all original cleanup (`ws.close()` + `gracely_cancel()` in the plugin's `finally` block) but suppresses the cancel-path stderr.

Both patches are installed once at module import and live at the top of `main.py`. They are necessary, not optional.

## `plugins/livekit-plugins-qwen/` — present but unused

The repo also ships `plugins/livekit-plugins-qwen/` (a Qwen Omni Realtime plugin). `main.py` does **not** import it; `tests/test_main_build_session.py::test_qwen_realtime_branch_removed` enforces this via `assert "qwen" not in src.lower()`. Keep the directory around as a reference for the previously-removed `qwen-realtime` pipeline mode.

## Why we do not call `volcengine.LLM`

The plugin's `LLM` class hits Volcengine Ark (`https://ark.cn-beijing.volces.com/api/v3/`) and would require `volcengine.llm` credentials. OpenVox instead uses `livekit-plugins-openai`'s `openai.LLM` (pinned to `==1.6.4` in `pyproject.toml`) pointed at the local Hermes api_server. See [Integrations → Hermes LLM](./hermes-llm.md).

## Source anchors

- `plugins/livekit-plugins-volcengine/pyproject.toml` — `livekit-agents==1.5.4` pin (the reason `--no-deps` matters)
- `plugins/livekit-plugins-volcengine/README.md` — upstream feature list (大模型 STT / TTS / LLM / Realtime)
- `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/__init__.py` — exports `["TTS", "LLM", "STT", "RealtimeModel", "__version__"]`
- `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/stt.py` — `STTOptions`, `_SpeechStream`, protocol header builder
- `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/tts.py` — `_TTSOptions`, HTTP chunked request + headers
- `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/llm.py` — dataclass-based `LLM` (not used by OpenVox)
- `main.py` lines 105–154 (the two `SpeechStream` patches)