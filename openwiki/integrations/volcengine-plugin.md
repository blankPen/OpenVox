---
type: Integration
title: Vendored `livekit-plugins-volcengine`(中文)
description: OpenVox 如何嵌入并打补丁 Volcengine STT/TTS/LLM/Realtime 插件,以及 kwarg 契约。
tags: [volcengine, plugin, vendored, stt, tts, llm]
---

# Vendored `livekit-plugins-volcengine`

OpenVox 在 `apps/voice-agent/plugins/livekit-plugins-volcengine/` 下面 ship 了一份 [`di-osc/livekit-plugins-chinese`](https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcinese) 的 vendored 副本。安装方式是 `pip install -e ... --no-deps`,这样仓库根 `pyproject.toml` 钉的 `livekit-agents[otel,silero,turn-detector]~=1.5` 就不会被 vendored 插件里 hard-pin 的 `livekit-agents==1.5.4` 覆盖。

## 为什么要 vendored 而不上 PyPI

- PyPI 上的 `livekit-plugins-volcengine==1.3.0` 用了位置参数 / `cluster=` 参数,与 vendored 这份冲突。两个一起装会因为 `sys.path` 顺序问题出现两份 `livekit.plugins.volcengine` 模块。
- Vendored 版用 keyword-only kwargs(`app_id=`、`access_token=`、`model=`、`api_key=`),与 `main.py` 的调用点对齐;PyPI 版早于这个约定。
- Vendored 插件在两个地方被 monkey-patch(见下文)。本地集中化让 patch 一目了然。

## 安装

```bash
pip install -e ./apps/voice-agent/plugins/livekit-plugins-volcengine --no-deps
```

`--no-deps` **必须**带。Vendored `pyproject.toml` 钉了 `livekit-agents==1.5.4`,如果不带 `--no-deps`,pip 会把宿主 venv 降到 1.5.4,搞坏根 `pyproject.toml` 里要求的 `[otel,silero,turn-detector]` extras。

## OpenVox 用到的插件面

`main.py` 只用 `plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/__init__.py` 里 5 个 export 中的 3 个:

| 类 | 模块 | OpenVox 传入的构造参数 |
|----|------|------------------------|
| `STT` | `stt.py` | `app_id=`、`access_token=` |
| `TTS` | `tts.py` | `app_id=`、`access_token=` |
| `LLM` | `llm.py` | (未使用 —— 见下方"为什么不用 `volcengine.LLM`") |
| `RealtimeModel` | `realtime.py` | (未使用 —— `tests/test_main_build_session.py::test_volcengine_realtime_branch_removed` 锁住) |

### `STT` — 流式语音识别

`plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/stt.py` 里 `STTOptions` 给出对接 Volcengine WebSocket `/api/v3/sauc/bigmodel` 接口的字段:

- 默认 `base_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"`
- 默认 `format = "pcm"`、`sample_rate = 16000`、`bits = 16`、`num_channels = 1`、`language = "zh-CN"`
- `model_name = "bigmodel"`、`result_type = "single"`、`enable_punc = True`
- 可选:`enable_itn`、`enable_ddc`、`show_utterance`、`vad_segment_duration` 等

OpenVox 只从 config 传 `app_id` + `access_token`,其余走默认。`volcengine.stt.*` schema 见 [Config loader](../configuration/config-loader.md)。

### `TTS` — 分块 HTTP 合成

`plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/tts.py` 调 Volcengine `/api/v3/tts/unidirectional` chunked HTTP 端点:

- 默认 `base_url = "https://openspeech.bytedance.com"`
- 默认 `voice = "zh_female_xiaohe_uranus_bigtts"`、`resource_id = "seed-tts-2.0"`
- 默认 `sample_rate = 24000`、`encoding = "pcm"`、`speed`/`volume`/`pitch` 都是 1.0

每个请求都带这些 header:

```text
Content-Type:        application/json
X-Api-App-Id:        <app_id>
X-Api-App-Key:       <app_id>
X-Api-Access-Key:    <access_token>
X-Api-Resource-Id:   <resource_id>
X-Api-Request-Id:    <shortuuid>   (if reqid is given)
```

OpenVox 只传 `app_id` + `access_token`。插件内部还会从 `os.environ["VOLCENGINE_TTS_ACCESS_TOKEN"]` 读 fallback(残留逻辑 —— OpenVox 从来不设这个 env)。

### `LLM` — Ark 网关客户端

`plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/llm.py` 用 dataclass 风格的 `LLM`,目标 `https://ark.cn-beijing.volces.com/api/v3/`,需要 `volcengine.llm` 一组凭证。**OpenVox 不调它**,LLM 一段走 `livekit-plugins-openai` 的 `openai.LLM`(根 `pyproject.toml` 钉 `==1.6.4`)指向本地 Hermes api_server。

### `RealtimeModel`

`realtime.py` 提供 `RealtimeModel`,基于 Volcengine 实时语音对话大模型(双向流)。OpenVox 不用;`tests/test_main_build_session.py::test_volcengine_realtime_branch_removed` 通过源码扫描锁住这一点。

## `main.py` 给 vendored 插件打的 patch

两处运行时 monkey-patch 落在 vendored STT 的 `livekit.plugins.volcengine.stt.SpeechStream` 上:

1. **`_process_stream_event` wrap** — 原方法跑完后,patch 用同模块的 `parse_response` 解码 payload,只在 `utterances[0].definite` 为 `True` 时打 `[用户语音] <text>`。这是运维侧"用户说了什么"的可视锚点;`logging.basicConfig` 不会自动展开 `extra={"text": ...}`,所以 patch 直接读已解析 payload。
2. **`_run` wrap** — `await _orig_stt_run(self)` 外层包 `try / except asyncio.CancelledError`,cancel 时静默 `return`。原 `_run` 在 `aiohttp.ClientSession.ws.receive()` 上起了一个嵌套 `recv_task`;`livekit-agents 1.6.x` 在子进程拆掉时 cancel 所有 task,无人 `await` 的 `_GatheringFuture` 异常会以 "exception was never retrieved" 形式污染 stderr。patch 保留插件原始 `finally` 里的 `ws.close()` + `gracely_cancel()` 清理,只屏蔽 cancel 路径的 stderr。

TTS 的 `SynthesizeStream._run` 也有一处对称的 patch(`_patched_tts_run`):HTTP 同步迭代器上没有 inner `recv_task` 可 drain,改为在 `_run` 入口 swallow `CancelledError`,让上层 gather 不再收到该异常。

三处 patch 都在 `main.py` 模块加载时一次性安装、集中在文件顶部。**必需,不是可选**。

## `plugins/livekit-plugins-qwen/` — 目录在但未用

仓库还 ship 了 `plugins/livekit-plugins-qwen/`(Qwen Omni Realtime 插件)。`main.py` **不 import 它**;`tests/test_main_build_session.py::test_qwen_realtime_branch_removed` 用 `assert "qwen" not in src.lower()` 锁住。保留目录作为之前移除的 `qwen-realtime` pipeline 模式的参考。

## 为什么不用 `volcengine.LLM`

`llm.py` 走的是 Volcengine Ark 网关(`https://ark.cn-beijing.volces.com/api/v3/`),需要 `volcengine.llm` 一组凭证;OpenVox 改用 `livekit-plugins-openai` 的 `openai.LLM` 指向本地 Hermes api_server,这样模型选型和密钥都跟 STT/TTS 解耦,LLM 替换不会牵动火山引擎那边。

## Source anchors

- `apps/voice-agent/plugins/livekit-plugins-volcengine/pyproject.toml` — `livekit-agents==1.5.4` pin(为什么 `--no-deps` 必须)
- `apps/voice-agent/plugins/livekit-plugins-volcengine/README.md` — 上游特性列表(大模型 STT / TTS / LLM / Realtime)
- `apps/voice-agent/plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/__init__.py` — export `["TTS", "LLM", "STT", "RealtimeModel", "__version__"]`
- `apps/voice-agent/plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/stt.py` — `STTOptions`、`_SpeechStream`、协议 header 构建
- `apps/voice-agent/plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/tts.py` — `_TTSOptions`、HTTP chunked 请求 + header
- `apps/voice-agent/plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/llm.py` — dataclass 风格 `LLM`(OpenVox 不调用)
- `apps/voice-agent/plugins/livekit-plugins-volcengine/livekit/plugins/volcengine/realtime.py` — `RealtimeModel`(OpenVox 不调用)
- `apps/voice-agent/main.py` 行 138–209(两处 `SpeechStream` patch + 一处 `SynthesizeStream` patch)