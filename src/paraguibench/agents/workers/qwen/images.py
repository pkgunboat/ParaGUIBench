"""为 Qwen 多模态请求有界缩放并重新编码桌面截图。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math

_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024
_MAX_IMAGE_EDGE = 8192
_MAX_DECODED_PIXELS = 32_000_000
_MAX_ASPECT_RATIO = 200
_IMAGE_FACTOR = 32


class QwenImageError(ValueError):
    """表示截图类型、尺寸或编码不满足 Qwen 请求边界。"""


@dataclass(frozen=True, slots=True)
class PreparedQwenScreenshot:
    """保存重新编码后的 PNG 和模型实际看到的像素尺寸。"""

    data: bytes
    width: int
    height: int
    media_type: str = "image/png"


def prepare_qwen_screenshot(
    screenshot: bytes,
    *,
    max_pixels: int,
) -> PreparedQwenScreenshot:
    """按 32 像素因子缩放截图并重新编码为 PNG。

    输入参数：
        screenshot：原始 PNG/JPEG bytes，最大 25 MiB。
        max_pixels：发送给模型的最大总像素数，范围 1024–16000000。
    输出返回值：
        PNG bytes 及其实际宽高；不会写磁盘或保留原始截图。
    异常：
        QwenImageError：输入、图片格式或尺寸不满足边界。
    """

    if (
        not isinstance(screenshot, bytes)
        or not screenshot
        or len(screenshot) > _MAX_SCREENSHOT_BYTES
    ):
        raise QwenImageError("screenshot 必须是最大 25 MiB 的非空 bytes")
    if (
        not isinstance(max_pixels, int)
        or isinstance(max_pixels, bool)
        or not _IMAGE_FACTOR**2 <= max_pixels <= 16_000_000
    ):
        raise QwenImageError("max_pixels 必须是 1024–16000000 的整数")

    try:
        from PIL import Image

        with Image.open(BytesIO(screenshot)) as source:
            if source.format not in {"PNG", "JPEG"}:
                raise QwenImageError("截图必须是 PNG 或 JPEG")
            width, height = source.size
            if (
                not 1 <= width <= _MAX_IMAGE_EDGE
                or not 1 <= height <= _MAX_IMAGE_EDGE
                or width * height > _MAX_DECODED_PIXELS
                or max(width, height) / min(width, height) > _MAX_ASPECT_RATIO
            ):
                raise QwenImageError("截图尺寸超出允许范围")
            source.load()
            target_width, target_height = _bounded_dimensions(
                width,
                height,
                max_pixels=max_pixels,
            )
            image = source.convert("RGB")
            if image.size != (target_width, target_height):
                image = image.resize(
                    (target_width, target_height),
                    Image.Resampling.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
    except QwenImageError:
        raise
    except Exception as error:
        raise QwenImageError(f"无法处理 Qwen 截图：{type(error).__name__}") from None
    encoded = buffer.getvalue()
    if len(encoded) > _MAX_SCREENSHOT_BYTES:
        raise QwenImageError("重新编码后的截图超过 25 MiB")
    return PreparedQwenScreenshot(
        data=encoded,
        width=target_width,
        height=target_height,
    )


def _bounded_dimensions(
    width: int,
    height: int,
    *,
    max_pixels: int,
) -> tuple[int, int]:
    """计算近似保持宽高比且不超过像素预算的 32 倍数尺寸。

    输入参数：
        width：原始图片宽度。
        height：原始图片高度。
        max_pixels：允许的最大总像素数。
    输出返回值：
        ``(target_width, target_height)``，两边均为 32 的正整数倍。
    """

    scale = min(1.0, math.sqrt(max_pixels / (width * height)))
    target_width = max(
        _IMAGE_FACTOR,
        int(round(width * scale / _IMAGE_FACTOR)) * _IMAGE_FACTOR,
    )
    target_height = max(
        _IMAGE_FACTOR,
        int(round(height * scale / _IMAGE_FACTOR)) * _IMAGE_FACTOR,
    )
    while target_width * target_height > max_pixels:
        if target_width >= target_height and target_width > _IMAGE_FACTOR:
            target_width -= _IMAGE_FACTOR
        elif target_height > _IMAGE_FACTOR:
            target_height -= _IMAGE_FACTOR
        else:
            break
    return target_width, target_height
