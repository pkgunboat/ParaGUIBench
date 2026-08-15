"""Qwen 截图缩放、重编码与资源边界测试。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from paraguibench.agents.workers.qwen.images import (
    QwenImageError,
    prepare_qwen_screenshot,
)


def _encoded_image(
    width: int,
    height: int,
    *,
    image_format: str = "PNG",
) -> bytes:
    """在内存中生成无敏感内容的测试图片。

    输入参数：
        width：测试图片宽度。
        height：测试图片高度。
        image_format：Pillow 输出格式，用于覆盖 PNG/JPEG 输入。
    输出返回值：
        可交给截图准备器的编码 bytes。
    """

    image = Image.new("RGB", (width, height), color=(16, 32, 64))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def test_qwen_image_is_reencoded_as_bounded_factor_32_png() -> None:
    """验证 JPEG 输入会被缩放为像素预算内的 32 倍数 PNG。

    输入参数：
        无；构造 1919×1079 的内存 JPEG 和 1000000 像素预算。
    输出返回值：
        无；重编码结果为 RGB PNG，宽高可被 32 整除且不超预算。
    """

    prepared = prepare_qwen_screenshot(
        _encoded_image(1919, 1079, image_format="JPEG"),
        max_pixels=1_000_000,
    )

    with Image.open(BytesIO(prepared.data)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (prepared.width, prepared.height)
    assert prepared.media_type == "image/png"
    assert prepared.width % 32 == 0
    assert prepared.height % 32 == 0
    assert prepared.width * prepared.height <= 1_000_000


@pytest.mark.parametrize(
    "screenshot",
    [
        b"not-an-image",
        pytest.param(
            _encoded_image(1, 201),
            id="aspect-ratio-over-200",
        ),
        pytest.param(
            _encoded_image(64, 64, image_format="GIF"),
            id="unsupported-gif",
        ),
    ],
)
def test_qwen_image_rejects_corruption_and_extreme_aspect_ratio(
    screenshot: bytes,
) -> None:
    """验证损坏图片和超长宽比在解码路径中 fail-closed。

    输入参数：
        screenshot：参数化的损坏图片或极端长宽比 PNG。
    输出返回值：
        无；两类输入均抛出稳定的 ``QwenImageError``。
    """

    with pytest.raises(QwenImageError):
        prepare_qwen_screenshot(screenshot, max_pixels=4_194_304)


@pytest.mark.parametrize("max_pixels", [0, 1023, 16_000_001, True])
def test_qwen_image_rejects_invalid_pixel_budgets(max_pixels: object) -> None:
    """验证像素预算必须是 1024–16000000 的非布尔整数。

    输入参数：
        max_pixels：参数化的越界数值或布尔值。
    输出返回值：
        无；不合法预算均在图片处理前被拒绝。
    """

    with pytest.raises(QwenImageError):
        prepare_qwen_screenshot(
            _encoded_image(64, 64),
            max_pixels=max_pixels,  # type: ignore[arg-type]
        )
