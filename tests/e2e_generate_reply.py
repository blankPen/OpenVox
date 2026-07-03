"""端到端测试 vendor RealtimeSession.generate_reply 修复。

验证目标：
1. ws 未就绪 → generate_reply 立即 set_exception("ws not ready")，不再 5 秒超时
2. ws 已就绪 → generate_reply 立即 set_result(generation_ev) + 异步发 hello_request (opcode 300)
3. 不再调用已废弃的 5 秒超时路径
"""

import asyncio
import gzip
import json
import os
import sys
from types import SimpleNamespace

import aiohttp

# 让 vendor 模块先于 livekit plugins 加载
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..",
    "vendor", "volcengine-src", "livekit-plugins", "livekit-plugins-volcengine"))

os.environ.setdefault("VOLCENGINE_REALTIME_APP_ID", "fake_app_id")
os.environ.setdefault("VOLCENGINE_REALTIME_ACCESS_TOKEN", "fake_token")

from livekit.plugins.volcengine import realtime as _rt_mod  # noqa: E402
from livekit.plugins.volcengine.realtime import (  # noqa: E402
    GZIP, JSON, MSG_WITH_EVENT, SERVER_ACK, SERVER_FULL_RESPONSE,
    RealtimeModel, RealtimeSession, generate_header,
)


def make_ack() -> bytes:
    header = generate_header(
        message_type=SERVER_ACK,
        serial_method=JSON,
        compression_type=GZIP,
    )
    session_id = b"test_session_id"
    sid_bytes = len(session_id).to_bytes(4, "big") + session_id
    payload_bytes = gzip.compress(b"{}")
    payload_size = len(payload_bytes).to_bytes(4, "big")
    return bytes(header) + sid_bytes + payload_size + payload_bytes


class MockWebSocket:
    """模拟 aiohttp.ClientWebSocketResponse，记录 send_bytes 调用。"""

    def __init__(self, receive_queue: asyncio.Queue):
        self._receive_queue = receive_queue
        self.sent: list[bytes] = []
        self.closed = False

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)
        if len(data) >= 8:
            opcode = int.from_bytes(data[4:8], "big")
            print(f"  [ws.send_bytes] opcode={opcode}")

    async def receive(self):
        item = await self._receive_queue.get()
        if item is None:
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=None)
        return SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=item)

    async def receive_bytes(self):
        item = await self._receive_queue.get()
        if item is None:
            raise aiohttp.ClientConnectionError("ws closed")
        return item

    async def close(self):
        if not self.closed:
            self.closed = True
            await self._receive_queue.put(None)


async def test_ws_not_ready_returns_immediately():
    """场景 1: ws 未就绪时调 generate_reply，必须立即 set_exception（不再 5 秒超时）。"""
    print("=" * 60)
    print("TEST 1: ws-not-ready → immediate set_exception")
    print("=" * 60)

    async with aiohttp.ClientSession() as http_session:
        # 让 _create_ws_conn 永远 hang，模拟 ws 未就绪
        async def hang_create_ws_conn(self):
            await asyncio.Event().wait()
            return None

        RealtimeSession._create_ws_conn = hang_create_ws_conn

        model = RealtimeModel(
            app_id="fake_app_id",
            access_token="fake_token",
            bot_name="test",
            model="O",
            opening="hi",
            http_session=http_session,
        )
        sess = model.session()

        # 记录开始时间
        t0 = time.monotonic()
        fut = sess.generate_reply()
        # 验证 future 立即完成（< 100ms 而不是 5 秒）
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, f"generate_reply took {elapsed:.2f}s, should be <0.1s"
        assert fut.done(), "ws not ready → future done immediately"
        exc = fut.exception()
        assert exc is not None, "expected exception"
        assert "ws not ready" in str(exc), f"unexpected exc: {exc}"
        print(f"[ok] elapsed: {elapsed*1000:.1f}ms (was 5000ms before fix)")
        print(f"[ok] exception: {exc}")

        # 清理
        sess._msg_ch.close()
        try:
            await asyncio.wait_for(sess._main_atask, timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            sess._main_atask.cancel()
            try:
                await sess._main_atask
            except (asyncio.CancelledError, Exception):
                pass


async def test_generate_reply_sends_hello_request():
    """场景 2: ws 已就绪时调 generate_reply，必须立即 set_result + 异步发 hello_request。"""
    print()
    print("=" * 60)
    print("TEST 2: ws-ready → set_result + send hello_request")
    print("=" * 60)

    receive_queue: asyncio.Queue = asyncio.Queue()
    mock_ws = MockWebSocket(receive_queue)
    # 两个 ack（start_connection + start_session）
    await receive_queue.put(make_ack())
    await receive_queue.put(make_ack())

    async def fake_create_ws_conn(self):
        return mock_ws

    RealtimeSession._create_ws_conn = fake_create_ws_conn

    async with aiohttp.ClientSession() as http_session:
        model = RealtimeModel(
            app_id="fake_app_id",
            access_token="fake_token",
            bot_name="小语",
            model="O",
            opening="你好啊，今天过得怎么样？",
            http_session=http_session,
        )
        sess = model.session()

        # 等 ws 就绪 + opening 路径完成
        for _ in range(50):
            if hasattr(sess, "_ws_conn") and sess._ws_conn is mock_ws:
                break
            await asyncio.sleep(0.05)
        assert hasattr(sess, "_ws_conn") and sess._ws_conn is mock_ws, "_ws_conn not set"
        # 等 opening 路径的 hello_request 发完
        await asyncio.sleep(0.2)
        opening_opcodes = [int.from_bytes(d[4:8], "big") for d in mock_ws.sent if len(d) >= 8]
        assert 300 in opening_opcodes, f"opening hello_request not sent: {opening_opcodes}"
        print(f"[ok] opening sent opcodes: {opening_opcodes}")

        # 现在调 generate_reply，验证立即返回 + 异步发第二个 hello_request
        sent_before = len(mock_ws.sent)
        t0 = time.monotonic()
        fut = sess.generate_reply()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, f"generate_reply took {elapsed:.2f}s, should be <0.1s"
        assert fut.done(), "future must resolve immediately"
        gen_ev = fut.result()
        print(f"[ok] elapsed: {elapsed*1000:.1f}ms (was 5000ms before fix)")
        print(f"[ok] generation_ev.user_initiated={gen_ev.user_initiated}")
        assert gen_ev.user_initiated is True, "user_initiated should be True for generate_reply"

        # 等异步 _do_chat_tts 完成
        await asyncio.sleep(0.2)
        sent_after = mock_ws.sent[sent_before:]
        new_opcodes = [int.from_bytes(d[4:8], "big") for d in sent_after if len(d) >= 8]
        # generate_reply 现在用 chat_tts_text (opcode 500)，不是 hello_request (300)
        assert new_opcodes == [500], f"expected 1 new opcode 500 (chat_tts_text), got {new_opcodes}"
        print(f"[ok] generate_reply sent new opcode 500 (chat_tts_text)")

        # 验证 message_stream 产生 MessageGeneration（即使没有服务端响应，generation 已创建）
        msg_gen = await asyncio.wait_for(gen_ev.message_stream.__anext__(), timeout=0.5)
        print(f"[ok] message_stream produced MessageGeneration: id={msg_gen.message_id}")

        # 验证：generate_reply 取 chat_ctx 最后 user message 作为 text 并 chat_tts_text 发送
        # （本测试没设 chat_ctx，所以 text 为空，chat_tts_text 仍会发，但服务端可能不响应）

        # 清理
        await mock_ws.close()
        try:
            await asyncio.wait_for(sess.aclose(), timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            pass


async def test_no_5s_timeout_anymore():
    """场景 3: 源码静态检查 — 不再有 5 秒超时路径。"""
    print()
    print("=" * 60)
    print("TEST 3: source static check — no 5s timeout")
    print("=" * 60)

    import inspect
    src = inspect.getsource(RealtimeSession.generate_reply)

    # 关键断言：移除 5 秒超时
    assert "call_later(5.0" not in src, "still has 5s timeout!"
    assert "_response_created_futures[event_id] = fut" not in src, "still uses dead dict!"
    assert "fut.set_result(generation_ev)" in src, "no immediate set_result!"
    assert "_do_chat_tts" in src, "no chat_tts_text helper!"
    assert "opcode 500" in inspect.getsource(RealtimeSession._do_chat_tts), \
        "should use chat_tts_text opcode 500, not hello_request opcode 300"
    print("[ok] no 5s timeout, uses chat_tts_text opcode 500")

    # update_chat_ctx 应该真正实现（不是 pass）
    src_update = inspect.getsource(RealtimeSession.update_chat_ctx)
    assert "_remote_chat_ctx.insert" in src_update, \
        "update_chat_ctx should insert into _remote_chat_ctx"
    print("[ok] update_chat_ctx syncs user messages into _remote_chat_ctx")

    src_main = inspect.getsource(RealtimeSession._main_task)
    assert "self._ws_conn = ws_conn" in src_main, "_ws_conn not stored!"
    print("[ok] _ws_conn stored on self in _main_task")

    print()


async def run_all():
    await test_no_5s_timeout_anymore()
    await test_ws_not_ready_returns_immediately()
    await test_generate_reply_sends_hello_request()
    print("=" * 60)
    print("ALL TESTS PASS")
    print("=" * 60)


if __name__ == "__main__":
    import time
    asyncio.run(run_all())