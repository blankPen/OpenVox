"""Tests for fs tools _sensitive module."""
from __future__ import annotations

from pathlib import Path

from workspace.extensions.tools.fs._sensitive import is_sensitive


def test_is_sensitive_detects_etc():
    assert is_sensitive("/etc/passwd") is True


def test_is_sensitive_detects_usr():
    assert is_sensitive("/usr/local/bin/foo") is True


def test_is_sensitive_detects_var():
    assert is_sensitive("/var/log/syslog") is True


def test_is_sensitive_detects_private_etc_macos():
    assert is_sensitive("/private/etc/hosts") is True


def test_is_sensitive_detects_home_ssh():
    ssh_dir = Path.home() / ".ssh" / "id_rsa"
    assert is_sensitive(str(ssh_dir)) is True


def test_is_sensitive_detects_home_aws():
    aws_dir = Path.home() / ".aws" / "credentials"
    assert is_sensitive(str(aws_dir)) is True


def test_is_sensitive_allows_tmp():
    assert is_sensitive("/tmp/hello.txt") is False


def test_is_sensitive_allows_workspace():
    assert is_sensitive("/Users/pz/workspace/livekit/main.py") is False


def test_is_sensitive_allows_relative_path():
    # 相对路径 resolve 后如果不是敏感路径 → False
    assert is_sensitive("hello.txt") is False