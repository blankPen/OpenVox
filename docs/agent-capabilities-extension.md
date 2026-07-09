# Agent 能力扩展指南

> 本文档梳理 `livekit-agents` 框架（当前版本 1.2.9）+ 火山引擎插件下的所有可扩展点，并给出首个方向（Function Tools）的最小可运行示例。

- **项目**：`/Users/pz/workspace/openvox` —— LiveKit Agents worker，语音服务走火山引擎
- **当前 Agent**：`VolcengineAgent`，仅使用了 `instructions` + `on_enter greeting`，等于只发挥了框架约 1/10 的能力
- **目标**：建立一份"想给 agent 加 X 能力时该往哪里动手"的索引

---

## 1. 扩展点全景

| #  | 扩展维度           | 用途                                          | 实现入口                                                | 典型场景                       |
|----|--------------------|-----------------------------------------------|---------------------------------------------------------|--------------------------------|
| 1  | **Function Tools** | 让 LLM 调用你的 Python 函数                  | `@function_tool` 装饰器 + `Agent(tools=[...])`          | 查天气、下单、查订单、控制 IoT |
| 2  | **MCP 服务器**     | 接入外部 MCP 工具生态                         | `Agent(mcp_servers=[...])` / session 级 `mcp_servers`   | GitHub / 文件系统 / Notion 等  |
| 3  | **生命周期 Hooks** | 加入/退出/用户发言完时拦截                    | 重写 `on_enter` / `on_exit` / `on_user_turn_completed`  | 欢迎语、退出时存对话、语义判断 |
| 4  | **Chat Context**   | 注入系统提示、用户偏好、长期记忆、Few-shot    | `self.update_chat_ctx()` / `instructions` 动态改        | 用户画像、多轮记忆、动态角色   |
| 5  | **多 Agent 编排**  | 在同一 session 切换不同 persona               | `session.update_agent(OtherAgent())`                    | 客服→技术专员转接              |
| 6  | **会话事件订阅**   | 监听 STT/LLM/TTS/function_call 各阶段         | `session.on("...", handler)`                            | 实时日志、token 用量、监控     |
| 7  | **自定义插件**     | 接入框架未覆盖的 STT/TTS/LLM/Realtime 模型    | 继承 `STT/TTS/LLM/RealtimeModel` 基类                   | 自研 ASR、私有模型对接         |
| 8  | **RAG / 知识库**   | 回答前先检索相关文档                          | 在 function_tool 里读向量库 / 全文索引                  | 内部知识库、产品手册助手       |
| 9  | **打断 & 端点**    | 控制 VAD / endpointing 行为                   | `min_endpointing_delay` / `turn_detection`              | 反应更快/更慢、避免抢话        |
| 10 | **session.userdata** | 多轮/多 agent 间传递自定义 Python 对象     | `session.userdata: UserdataModel`                       | 订单状态、身份验证上下文       |

### 优先级建议

- **最快出价值**：方向 1（Function Tools）—— 一两小时就能让 agent 真正"做事"
- **最高杠杆**：方向 2（MCP）—— 一行配置接入整个工具生态
- **最容易被忽略**：方向 4（Chat Context 动态注入）—— 是"有记忆 agent"的关键
- **最深度**：方向 7（自定义插件）—— 仅当火山引擎插件不满足某个底层需求时才做

---

## 2. Function Tools（首选方向）详解

### 2.1 核心机制

```
用户语音 ──► RealtimeModel(豆包) ──► 检测到需要 tool ──► livekit-agents 调度 ──► 你的 Python 函数
                                                                  │
                                                                  ▼
                              TTS 播报 ◄── RealtimeModel 收到结果 ◄── 函数返回值
```

**事实校验**（已查 `vendor/volcengine-src/.../volcengine/realtime.py`）：

- `RealtimeModel.tools` 属性（line 782）返回当前 tool 上下文
- `RealtimeModel.update_tools()`（line 793）支持运行时增删
- 协议层 `_create_session_update_event`（line 709）把 tool 列表发给豆包
- 配置 `manual_function_calls=True`（line 290）：tool 调用由 livekit-agents 框架调度，不在 realtime API 内部循环

> ✅ 火山引擎 Realtime 模式下**完全支持 function tool**，不需要切到 pipeline。

### 2.2 关键行为（livekit-agents 1.2.9）

- `@function_tool` 装饰器把函数签名 + docstring 自动转成 JSON schema，发给 LLM
- 异步 / 同步函数都支持
- 返回 `str` 或 `dict`（dict 自动 JSON 序列化）
- `tools=[...]` 传给 `Agent(...)` 即可生效
- `agent.update_tools(...)` 可运行时增删
- `Annotated[type, "..."]` 类型注解可补充参数描述（更友好的 schema）

### 2.3 最小可运行 demo

直接替换 `main.py` 中的 `VolcengineAgent` 类：

```python
import datetime
from typing import Annotated
from livekit.agents import Agent, function_tool


class VolcengineAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "你是一个友好的中文语音助手，名字叫小语。"
                "你可以：\n"
                "1. 用 get_current_time 查当前时间\n"
                "2. 用 get_weather 查指定城市天气（支持 北京/上海/广州/深圳）\n"
                "3. 用 calculate 做简单加减乘除\n"
                "请根据用户问题选择合适的工具。回答简洁自然，避免表情符号。"
            ),
            tools=[
                self.get_current_time,
                self.get_weather,
                self.calculate,
            ],
        )

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions="用一句话向用户问好，并告诉用户你能查时间、天气、做计算。"
        )

    # --- Function Tools ---
    # docstring → LLM 看到的工具描述
    # 类型注解 → JSON schema

    @function_tool()
    async def get_current_time(self) -> str:
        """获取当前的日期和时间。"""
        now = datetime.datetime.now()
        return now.strftime("现在是 %Y 年 %m 月 %d 日 %H:%M:%S")

    @function_tool()
    async def get_weather(self, city: Annotated[str, "城市名称，例如：北京"]) -> str:
        """查询指定城市的当前天气。

        Args:
            city: 城市名，仅支持 北京/上海/广州/深圳
        """
        # 演示用 mock 数据；生产接和风天气/彩云 API
        mock = {
            "北京": "晴，25°C，空气质量良",
            "上海": "多云，28°C，有阵雨",
            "广州": "雷阵雨，31°C，湿度大",
            "深圳": "晴，30°C，海风舒适",
        }
        return mock.get(city, f"暂时没有 {city} 的天气数据，先告诉你别的吧。")

    @function_tool()
    async def calculate(self, expression: Annotated[str, "数学表达式，例如：12*5+3"]) -> str:
        """计算简单的数学表达式，仅支持 + - * / 和括号。

        Args:
            expression: 要计算的数学表达式
        """
        try:
            # ⚠️ 生产环境不要直接 eval；这里演示用 ast 白名单
            import ast
            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Expression, ast.BinOp,
                                         ast.UnaryOp, ast.Constant, ast.Num)):
                    raise ValueError("包含不支持的语法")
            result = eval(compile(tree, "<expr>", "eval"))
            return f"{expression} = {result}"
        except Exception as e:
            return f"算不了，{e}"
```

### 2.4 验证步骤

**第一步：静态校验（不依赖网络）**

```bash
source .venv/bin/activate
python -c "
from main import VolcengineAgent
a = VolcengineAgent()
print('Tool count:', len(a.tools))
for t in a.tools:
    print(f'  - {t.info.name}: {t.info.description[:50]}')
"
```

预期输出：`Tool count: 3` + 三个 tool 的名字和描述。

**第二步：console 模式实测**

```bash
python main.py console
```

对着麦克风说：

- "现在几点了？" → agent 播报当前时间
- "北京今天热不热？" → 调用 `get_weather("北京")`
- "25 乘以 4 等于多少？" → 调用 `calculate("25*4")`

**第三步：看日志确认 function call 路径**

在 `main.py` 临时加：

```python
@session.on("function_calls_executed")
def on_tools(ev):
    logger.info(f"[TOOL] called: {[c.name for c in ev.function_calls]}")
```

预期日志：`[TOOL] called: ['get_weather']` 等。

### 2.5 进阶玩法

1. **异步外部调用**：`async with aiohttp.ClientSession() as s: await s.get(...)` —— 装饰器对 async 一等公民
2. **返回结构化数据**：返回 `dict`，框架自动 JSON 序列化，LLM 自己组织语言
3. **运行时增删**：`session.current_agent.update_tools([new_tool])` 不重启加新能力
4. **上下文相关工具**：在 `on_enter` 里读 `session.userdata` 判断用户身份，注册不同工具集
5. **错误处理**：函数内 raise 会被框架捕获并以错误信息喂回 LLM，LLM 会向用户解释失败

### 2.6 常见坑

| 现象                                         | 原因                                           | 解决                                                                 |
|----------------------------------------------|------------------------------------------------|----------------------------------------------------------------------|
| realtime 模式下豆包"看到 tool 但不调用"       | `instructions` 里没明示可用工具                | 在 system prompt 里明确说"你可以用 get_xxx 工具..."                  |
| Tool schema 不符合预期                       | 缺 docstring 或缺类型注解                     | 每个 `@function_tool` 函数必须写 docstring 和参数类型                 |
| 复杂表达式计算失败                           | `eval` 安全风险                                | 生产用 `ast` 解析或专门的数学库（如 `sympy`）                        |
| Tool 调用阻塞主事件循环                      | 工具函数是同步阻塞调用                         | 改 `async def`，把阻塞 IO 换成 `aiohttp`/`asyncio.to_thread`         |

---

## 3. 其他方向的快速索引（demo 跑通后再展开）

### 3.1 MCP 服务器接入（方向 2）

```python
from livekit.agents import Agent
from mcp import StdioServerParams

class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="...",
            mcp_servers=[
                StdioServerParams(command="uvx", args=["mcp-server-git"]),
            ],
        )
```

接入现成 MCP 工具生态，无需自己写 function tool。

### 3.2 Chat Context 动态注入（方向 4）

```python
async def on_enter(self):
    # 从某处读取用户画像/历史
    user_profile = await load_profile(self.session.userdata.user_id)

    # 注入到系统提示
    self.update_instructions(
        self.instructions + f"\n用户画像：{user_profile.summary}"
    )

    # 或直接追加 chat 上下文
    self.session.update_chat_ctx(
        messages=[{"role": "system", "content": f"用户偏好：{user_profile.prefs}"}]
    )
```

### 3.3 多 Agent 编排（方向 5）

```python
# 主 agent 检测到"技术问题"关键词
async def on_user_turn_completed(self, turn, new_messages):
    if "技术问题" in turn.transcript:
        await self.session.update_agent(TechSupportAgent())

# TechSupportAgent 是另一个 Agent 子类，独立的 instructions/tools
```

### 3.4 会话事件订阅（方向 6）

```python
def entrypoint(ctx):
    session = _build_session()

    @session.on("metrics_collected")
    def on_metrics(ev):
        logger.info(f"[METRICS] {ev.metrics}")

    @session.on("agent_state_changed")
    def on_state(ev):
        logger.info(f"[STATE] {ev.old_state} → {ev.new_state}")

    await session.start(agent=VolcengineAgent(), room=ctx.room)
```

常用事件：`metrics_collected`、`agent_state_changed`、`user_state_changed`、`function_calls_executed`、`error`。

### 3.5 session.userdata（方向 10）

```python
from pydantic import BaseModel

class UserContext(BaseModel):
    user_id: str
    auth_token: str
    order_id: str | None = None

# 在 entrypoint 里设置
session.userdata = UserContext(user_id="u123", auth_token="...")

# 在 agent 工具里读
@function_tool()
async def get_my_orders(self) -> str:
    uid = self.session.userdata.user_id
    ...
```

---

## 4. 落地路线建议

按 ROI 排序，建议按下列顺序逐个突破：

1. **Function Tools**（本文档已给 demo）—— 1~2 小时，价值最高
2. **生命周期 Hooks**（方向 3）—— 接 demo 自然延伸，半小时
3. **会话事件订阅**（方向 6）—— 加日志/监控必备，一小时
4. **Chat Context 动态注入**（方向 4）—— 开启"有记忆 agent"，半天
5. **MCP 服务器**（方向 2）—— 工具生态扩展，一小时
6. **多 Agent 编排**（方向 5）—— 复杂业务场景，1~2 天
7. **session.userdata**（方向 10）—— 与方向 1/4/5 配套，自然引入
8. **RAG / 知识库**（方向 8）—— 独立项目，1 周+
9. **打断 & 端点调参**（方向 9）—— 体验打磨，按需
10. **自定义插件**（方向 7）—— 仅当现有插件不满足时

---

## 5. 参考链接

- Plugin 源码：<https://github.com/di-osc/livekit-plugins-chinese/tree/main/livekit-plugins/livekit-plugins-volcengine>
- livekit-agents 官方仓库：<https://github.com/livekit/agents>
- Agent 基础示例：<https://github.com/livekit/agents/blob/main/examples/voice_agents/basic_agent.py>
- MCP 规范：<https://modelcontextprotocol.io/>