"""OSWorld 图像评测的轻量、可独立测试实现。"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter


def _normalized_histogram(
    values: np.ndarray,
    *,
    bins: int = 32,
    mask: Optional[np.ndarray] = None,
) -> Optional[np.ndarray]:
    """
    计算 0~255 像素值的归一化直方图。

    输入:
        values: 任意形状的像素通道数组。
        bins: 直方图区间数。
        mask: 可选布尔掩码，仅统计有效像素。
    输出:
        总和为 1 的直方图；没有有效像素时返回 ``None``。
    """
    selected = values[mask] if mask is not None else values.reshape(-1)
    if selected.size == 0:
        return None
    hist, _ = np.histogram(selected, bins=bins, range=(0, 256))
    total = float(hist.sum())
    return hist.astype(np.float64) / total if total else None


def _histogram_intersection(
    first: Optional[np.ndarray],
    second: Optional[np.ndarray],
) -> float:
    """
    计算两个归一化直方图的交集得分。

    输入:
        first / second: 归一化直方图或 ``None``。
    输出:
        ``[0, 1]`` 得分；双方均无有效样本视为一致，仅一方为空视为不一致。
    """
    if first is None and second is None:
        return 1.0
    if first is None or second is None:
        return 0.0
    return float(np.minimum(first, second).sum())


def _hsv_color_similarity(first: Image.Image, second: Image.Image) -> float:
    """
    比较两张 RGB 图像的色相与饱和度分布，抑制等灰度异色误判。

    输入:
        first / second: 尺寸一致的 RGB PIL 图像。
    输出:
        色相、饱和度直方图交集的平均值，范围为 ``[0, 1]``。
    """
    hsv1 = np.asarray(first.convert("HSV"), dtype=np.uint8)
    hsv2 = np.asarray(second.convert("HSV"), dtype=np.uint8)
    saturation1 = hsv1[..., 1]
    saturation2 = hsv2[..., 1]

    saturation_score = _histogram_intersection(
        _normalized_histogram(saturation1),
        _normalized_histogram(saturation2),
    )
    chromatic1 = saturation1 >= 16
    chromatic2 = saturation2 >= 16
    hue_score = _histogram_intersection(
        _normalized_histogram(hsv1[..., 0], mask=chromatic1),
        _normalized_histogram(hsv2[..., 0], mask=chromatic2),
    )
    return (saturation_score + hue_score) / 2.0


def _rgb_structural_similarity(first: np.ndarray, second: np.ndarray) -> float:
    """
    以 7×7 局部窗口计算 RGB 三通道平均 SSIM。

    输入:
        first / second: 尺寸一致的 uint8 RGB 数组。
    输出:
        三通道 SSIM 平均值，范围裁剪到 ``[0, 1]``。
    """
    first_float = first.astype(np.float64)
    second_float = second.astype(np.float64)
    channel_scores = []
    constant1 = (0.01 * 255) ** 2
    constant2 = (0.03 * 255) ** 2
    for channel in range(3):
        image1 = first_float[..., channel]
        image2 = second_float[..., channel]
        mean1 = uniform_filter(image1, size=7, mode="reflect")
        mean2 = uniform_filter(image2, size=7, mode="reflect")
        variance1 = uniform_filter(image1 * image1, size=7, mode="reflect") - mean1 ** 2
        variance2 = uniform_filter(image2 * image2, size=7, mode="reflect") - mean2 ** 2
        covariance = uniform_filter(image1 * image2, size=7, mode="reflect") - mean1 * mean2
        numerator = (2 * mean1 * mean2 + constant1) * (2 * covariance + constant2)
        denominator = (mean1 ** 2 + mean2 ** 2 + constant1) * (
            variance1 + variance2 + constant2
        )
        channel_scores.append(float(np.mean(numerator / denominator)))
    return float(np.clip(np.mean(channel_scores), 0.0, 1.0))


def compare_images(image1_path: str, image2_path: str, **_options: object) -> float:
    """
    以彩色 SSIM 与 HSV 分布共同比较两张图像。

    输入:
        image1_path: agent 结果图像路径。
        image2_path: gold 图像路径。
        **_options: 为统一 dispatcher 签名保留；当前不消费额外选项。
    输出:
        ``[0, 1]`` 连续相似度。空间结构和彩色分布任一较差都会限制最终分数。
    """
    if not image1_path or not image2_path:
        return 0.0

    with Image.open(image1_path) as raw1, Image.open(image2_path) as raw2:
        image1 = raw1.convert("RGB")
        image2 = raw2.convert("RGB")
        new_size = (
            min(image1.width, image2.width),
            min(image1.height, image2.height),
        )
        if min(new_size) < 7:
            return 0.0
        image1 = image1.resize(new_size, Image.Resampling.LANCZOS)
        image2 = image2.resize(new_size, Image.Resampling.LANCZOS)

        array1 = np.asarray(image1, dtype=np.uint8)
        array2 = np.asarray(image2, dtype=np.uint8)
        spatial_score = _rgb_structural_similarity(array1, array2)
        color_score = _hsv_color_similarity(image1, image2)

    return float(np.clip(min(spatial_score, color_score), 0.0, 1.0))
