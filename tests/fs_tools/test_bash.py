"""bash 完整测试矩阵。Spec §3.3 / §5.2 / §5.4。"""
from __future__ import annotations

import asyncio
import logging

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_bash_echo():
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("echo hello"))

    assert "hello" in result
    assert result.startswith("[EXIT 0]")


def test_bash_non_zero_exit():
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("false"))

    assert result.startswith("[EXIT 1]")


def test_bash_timeout():
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("sleep 5", timeout=1))

    assert result.startswith("[TIMEOUT]")


def test_bash_invalid_timeout():
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("echo hi", timeout=0))
    assert result.startswith("[ERROR]")
    assert "timeout" in result

    result = _run(bash("echo hi", timeout=301))
    assert result.startswith("[ERROR]")
    assert "timeout" in result


def test_bash_cwd(tmp_path):
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("pwd", cwd=str(tmp_path)))

    assert str(tmp_path) in result
    assert result.startswith("[EXIT 0]")


def test_bash_cwd_not_exist():
    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("echo hi", cwd="/nonexistent/path"))

    assert result.startswith("[ERROR]")


def test_bash_does_not_inherit_secret_env(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "supersecret")

    from workspace.extensions.tools.fs.bash import bash

    result = _run(bash("echo MY_SECRET=$MY_SECRET"))

    assert "supersecret" not in result


def test_bash_pipes_and_chains():
    from workspace.extensions.tools.fs.bash import bash

    # shell 优先级：| 优先于 &&，所以解析为 echo a && (echo b | tr a-z A-Z)
    result = _run(bash("echo a && echo b | tr a-z A-Z"))

    assert "a" in result
    assert "B" in result
    assert result.startswith("[EXIT 0]")


def test_bash_warning_logged(caplog):
    import logging
    from workspace.extensions.tools.fs.bash import bash
    with caplog.at_level(logging.WARNING, logger="volcengine-agent"):
        _run(bash("echo hi"))
    assert "BASH_OP" in caplog.text