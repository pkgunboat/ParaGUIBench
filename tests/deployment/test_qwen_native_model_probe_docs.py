"""Qwen native 模型探针部署命令与依赖树文档测试。"""

from __future__ import annotations

from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_qwen_native_model_probe_is_documented_as_safe_no_vm_gate() -> None:
    """验证部署指南和依赖树共同固定探针的安全边界。

    输入参数：
        无；读取仓库中的 OSWorld 部署文档和两份依赖树。
    输出返回值：
        无；文档必须包含命令、固定输出、shell trace 保护，
        以及不进入 VM/controller/RunStore 的依赖声明。
    """

    deployment = (
        _REPOSITORY_ROOT / "docs" / "deployment" / "osworld-linux.md"
    ).read_text(encoding="utf-8")
    architecture = (
        _REPOSITORY_ROOT / "docs" / "architecture" / "dependency-tree.md"
    ).read_text(encoding="utf-8")
    installation = (
        _REPOSITORY_ROOT / "docs" / "installation" / "dependency-tree.md"
    ).read_text(encoding="utf-8")
    environment_template = (_REPOSITORY_ROOT / ".env.example").read_text(
        encoding="utf-8"
    )

    assert "set +x" in deployment
    assert "paraguibench_cleanroom model-probe qwen-native" in deployment
    assert "PASS qwen-native-computer-use" in deployment
    assert "FAIL ProbeConfigurationError" in deployment
    assert "FAIL QwenActionRejectedError" in deployment
    assert "FAIL QwenModelError" in deployment
    assert "FAIL ProbeInternalError" in deployment
    assert "cli model-probe qwen-native" in architecture
    assert "QwenOpenAIModel.next_action" in architecture
    assert "VM/controller/RunStore" in architecture
    assert "model-probe qwen-native" in installation
    assert "openai + Pillow" in installation
    assert "PARAGUIBENCH_MODEL_ID=\n" in environment_template
