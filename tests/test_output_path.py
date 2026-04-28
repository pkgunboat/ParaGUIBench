"""
BasePipeline._resolve_output_json_path 单元测试。

验证三条分支:
    1. --final 显式覆盖时不注入 host_tag
    2. --output-json-path 显式覆盖时不注入 host_tag
    3. 默认分支注入 host_tag → logs/<host_tag>/<pipeline>_<ts>/results.json
"""

import argparse
import os

import pytest

from pipeline_base import BasePipeline


class _FakeBasePipeline:
    """
    最小桩对象，用于直接调用 BasePipeline._resolve_output_json_path
    而不构造完整的 BasePipeline（避免拉起重型依赖）。
    """

    def __init__(self, args, output_dir_override=None, pipeline_name="qa"):
        self.args = args
        self.output_dir_override = output_dir_override
        self.pipeline_name = pipeline_name


def _make_args(*, final="", output_json_path=""):
    return argparse.Namespace(final=final, output_json_path=output_json_path)


def test_final_branch_does_not_inject_host_tag(tmp_path, monkeypatch):
    """--final 显式覆盖：直接写入 final 目录，不加 host_tag。"""
    monkeypatch.setenv("PARABENCH_HOST_TAG", "test-host")
    final_dir = tmp_path / "explicit-final"
    self_obj = _FakeBasePipeline(_make_args(final=str(final_dir)))

    path = BasePipeline._resolve_output_json_path(self_obj)

    assert path == os.path.join(str(final_dir), "qa_results.json")
    assert "test-host" not in path
    assert os.path.isdir(str(final_dir))


def test_output_json_path_branch_does_not_inject_host_tag(tmp_path, monkeypatch):
    """--output-json-path 显式覆盖：直接使用用户给定路径，不加 host_tag。"""
    monkeypatch.setenv("PARABENCH_HOST_TAG", "test-host")
    explicit = tmp_path / "custom" / "myresults.json"
    self_obj = _FakeBasePipeline(_make_args(output_json_path=str(explicit)))

    path = BasePipeline._resolve_output_json_path(self_obj)

    assert path == str(explicit)
    assert "test-host" not in path
    assert os.path.isdir(str(explicit.parent))


def test_output_dir_override_does_not_inject_host_tag(tmp_path, monkeypatch):
    """output_dir_override（程序化注入）显式覆盖：不加 host_tag。"""
    monkeypatch.setenv("PARABENCH_HOST_TAG", "test-host")
    override_dir = tmp_path / "override"
    self_obj = _FakeBasePipeline(
        _make_args(),
        output_dir_override=str(override_dir),
        pipeline_name="webmall",
    )

    path = BasePipeline._resolve_output_json_path(self_obj)

    assert path == os.path.join(str(override_dir), "webmall_results.json")
    assert "test-host" not in path


def test_default_branch_injects_host_tag(tmp_path, monkeypatch):
    """
    默认分支（无任何显式覆盖）：路径形如 logs/<host_tag>/<pipeline>_<ts>/results.json。
    通过 monkeypatch UBUNTU_ENV_DIR 让生成的 logs 落到 tmp_path，避免污染仓库。
    """
    monkeypatch.setenv("PARABENCH_HOST_TAG", "unit-test-host")
    monkeypatch.setattr("pipeline_base.UBUNTU_ENV_DIR", str(tmp_path))

    self_obj = _FakeBasePipeline(_make_args(), pipeline_name="qa")

    path = BasePipeline._resolve_output_json_path(self_obj)

    parts = path.split(os.sep)
    # 末尾形如 ['logs', 'unit-test-host', 'qa_<ts>', 'results.json']
    assert parts[-1] == "results.json"
    assert parts[-2].startswith("qa_")
    assert parts[-3] == "unit-test-host"
    assert parts[-4] == "logs"
    assert path.startswith(str(tmp_path))
    assert os.path.isdir(os.path.dirname(path))


def test_default_branch_uses_pipeline_name_in_dir(tmp_path, monkeypatch):
    """不同 pipeline_name 应反映在中间目录名上。"""
    monkeypatch.setenv("PARABENCH_HOST_TAG", "h")
    monkeypatch.setattr("pipeline_base.UBUNTU_ENV_DIR", str(tmp_path))

    self_obj = _FakeBasePipeline(_make_args(), pipeline_name="webnavigate")
    path = BasePipeline._resolve_output_json_path(self_obj)

    assert os.path.basename(os.path.dirname(path)).startswith("webnavigate_")
