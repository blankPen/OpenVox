# OpenWiki 生成指令稿

本文件由本仓库维护者手写,不被 OpenWiki 重新生成。OpenWiki 在 `--init` / `--update`
时读取本文件作为 wiki 内容生成的唯一指令。

## 语言

**所有 OpenWiki 生成的 wiki 页面必须使用中文(zh-CN)输出。** 包含但不限于:

- 段落正文、列表项、表格单元格
- Markdown front matter 中的 `title` / `description` 字段(允许保留英文专有名词如 API/SDK/Volcengine/LiveKit)
- Mermaid 图表的 `participant` / 节点标签 / 状态名(同样保留英文专有名词)

专有名词、API 路径、代码标识符、文件名保留原文不动。

## 风格

- 简洁、可执行、新人友好——给读者一份"从哪里开始"的导航,而不是百科全书。
- Mermaid 图表优先(sequence / flowchart / stateDiagram / ER),只在能澄清概念时画。
- 引用源文件用相对仓库路径(如 `apps/voice-agent/main.py`)。
- 中文标点符号全角;代码块内保持半角。

## 必覆盖的概念清单

OpenWiki 每次 `--update` 时都要保证 `openwiki/` 目录下能回答下面这些问题:

1. **整体架构 4 块图**:Flutter 客户端 → LiveKit Server → voice-agent worker → Volcengine
2. **跨端契约入口**:`shared/agent-protocol.md`、`shared/room-naming.md`、`shared/livekit-claims.example.json`
3. **`main.py` 关键 hook**:`on_enter` 开场白、`_custom_text_input_cb` 文本输入、三处日志去重 monkey-patch
4. **Volcengine 插件入口**:`apps/voice-agent/plugins/livekit-plugins-volcengine/`,STT/TTS/LLM 三段
5. **已知坑索引**:链接到 `apps/voice-agent/CLAUDE.md` 的「已知坑」章节,**不复述细节**,只列索引与一句话解释

## 不要做的事

- 不要重复 `apps/voice-agent/CLAUDE.md` 和 `apps/voice-client/CLAUDE.md` 已经写过的细节(那些是手写文档,OpenWiki 不碰)。
- 不要生成 emoji、表情符号、装饰性 ASCII art。
- 不要在 `<!-- OPENWIKI:START -->` 区块之外修改 `CLAUDE.md` 或 `AGENTS.md`。

## 更新策略

- `--update` 只更新 `<!-- OPENWIKI:START -->` 区块,不要触碰手写内容。
- 新增概念 commit 进 `openwiki/`,不进 `CLAUDE.md`。

## OPENWIKI 区块(`<!-- OPENWIKI:START -->` 包裹部分)的内容要求

仓库根 `CLAUDE.md` 中的 `<!-- OPENWIKI:START -->` ... `<!-- OPENWIKI:END -->` 块,以及 `AGENTS.md` 中同样区块,**全部使用中文(zh-CN)**。专有名词(OpenWiki / GitHub Actions / LiveKit / Volcengine 等)保留英文不动。
