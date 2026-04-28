"""
get_host_tag() 单元测试。

验证三条优先级:
    1. PARABENCH_HOST_TAG 环境变量优先于 socket.gethostname()
    2. 无环境变量时回退到 socket.gethostname() 短主机名
    3. 输入含特殊字符 / 空白时被正确合法化
"""

import os

import pytest

from pipelines._host_tag import _normalize, get_host_tag


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """每个测试前清掉 PARABENCH_HOST_TAG，避免外部污染。"""
    monkeypatch.delenv("PARABENCH_HOST_TAG", raising=False)


def test_env_var_takes_precedence(monkeypatch):
    monkeypatch.setenv("PARABENCH_HOST_TAG", "Mac-Laptop.local")
    assert get_host_tag() == "mac-laptop-local"


def test_env_var_with_special_chars(monkeypatch):
    monkeypatch.setenv("PARABENCH_HOST_TAG", "  Server B / 192.168 !! ")
    assert get_host_tag() == "server-b-192-168"


def test_env_var_all_invalid_falls_back(monkeypatch):
    """全是非法字符时退化为 unknown-host。"""
    monkeypatch.setenv("PARABENCH_HOST_TAG", "!!!")
    assert get_host_tag() == "unknown-host"


def test_no_env_uses_hostname(monkeypatch):
    """无环境变量时取 hostname 短名。"""
    monkeypatch.setattr("socket.gethostname", lambda: "my-server.example.com")
    assert get_host_tag() == "my-server"


def test_no_env_hostname_uppercase(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "BIG-SERVER")
    assert get_host_tag() == "big-server"


@pytest.mark.parametrize("raw, expected", [
    ("Foo.Bar_BAZ-1", "foo-bar_baz-1"),
    ("", "unknown-host"),
    ("   ", "unknown-host"),
    ("---", "unknown-host"),
    ("a/b\\c", "a-b-c"),
    ("multi  space", "multi-space"),
    ("trailing-", "trailing"),
    ("-leading", "leading"),
])
def test_normalize(raw, expected):
    assert _normalize(raw) == expected
