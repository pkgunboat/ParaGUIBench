"""WebMall logical URL 与当前部署地址之间的双向注册表。"""

from __future__ import annotations

from collections.abc import Mapping
import re
from urllib.parse import SplitResult, urlsplit, urlunsplit

_LOGICAL_ORIGIN_PATTERN = re.compile(
    r"webmall://(?P<store_id>[A-Za-z0-9][A-Za-z0-9-]*)"
)


class WebMallURLRegistryError(ValueError):
    """表示 WebMall URL 不能被安全映射。"""


class WebMallURLRegistry:
    """维护稳定 store ID 与部署期 runtime origin 的双向映射。"""

    def __init__(self, origins: Mapping[str, str]) -> None:
        """构造 WebMall 地址注册表。

        输入参数：
            origins：logical store ID 到当前部署 HTTP(S) origin 的映射。
        输出返回值：
            无；构造函数在实例内建立正向和反向索引。
        """

        self._origins = dict(origins)
        self._stores_by_origin: dict[str, str] = {}
        for store_id, origin in self._origins.items():
            origin_parts = urlsplit(origin)
            _validate_origin(origin_parts)
            origin_key = _origin_key(origin_parts)
            if origin_key in self._stores_by_origin:
                raise WebMallURLRegistryError(
                    "多个 WebMall store 不能共享同一个 runtime origin"
                )
            self._stores_by_origin[origin_key] = store_id

    def materialize_url(self, logical_url: str) -> str:
        """把一个 ``webmall://`` logical URL 转为当前部署 URL。

        输入参数：
            logical_url：以 store ID 为 authority 的 canonical WebMall URL。
        输出返回值：
            使用当前部署 scheme 和 authority、且保留原始 path/query/fragment
            的 HTTP(S) URL。
        异常：
            WebMallURLRegistryError：URL 不是 logical WebMall URL，或 store
            未配置。
        """

        logical = urlsplit(logical_url)
        if logical.scheme != "webmall" or logical.netloc not in self._origins:
            raise WebMallURLRegistryError("logical WebMall URL 的 store 未配置")
        runtime_origin = urlsplit(self._origins[logical.netloc])
        return urlunsplit(
            (
                runtime_origin.scheme,
                runtime_origin.netloc,
                logical.path,
                logical.query,
                logical.fragment,
            )
        )

    def canonicalize_url(self, runtime_url: str) -> str:
        """把当前部署 URL 反向转换为稳定的 ``webmall://`` URL。

        输入参数：
            runtime_url：Agent 或浏览器返回的当前部署 HTTP(S) URL。
        输出返回值：
            使用 store ID 且保留原始 path/query/fragment 的 logical URL。
        异常：
            WebMallURLRegistryError：runtime origin 不属于任何已配置 store。
        """

        runtime = urlsplit(runtime_url)
        store_id = self._stores_by_origin.get(_origin_key(runtime))
        if store_id is None:
            raise WebMallURLRegistryError("runtime WebMall URL 的 origin 未配置")
        return urlunsplit(
            ("webmall", store_id, runtime.path, runtime.query, runtime.fragment)
        )

    def materialize_text(self, text: str) -> str:
        """替换自由文本中的全部 WebMall logical origin。

        输入参数：
            text：可能含一个或多个 ``webmall://<store-id>`` 的文本。
        输出返回值：
            仅 origin 被替换为当前部署地址的新文本；其余字符逐字保留。
        异常：
            WebMallURLRegistryError：文本引用了未配置的 store ID。
        """

        def replace_origin(match: re.Match[str]) -> str:
            """把单个正则命中的 store ID 转成部署 origin。

            输入参数：
                match：包含 ``store_id`` 命名组的 logical origin 匹配。
            输出返回值：
                去除可选末尾斜杠的部署 origin。
            异常：
                WebMallURLRegistryError：store ID 不在当前注册表中。
            """

            store_id = match.group("store_id")
            runtime_origin = self._origins.get(store_id)
            if runtime_origin is None:
                raise WebMallURLRegistryError(
                    "instruction 引用了未配置的 WebMall store"
                )
            return runtime_origin.rstrip("/")

        return _LOGICAL_ORIGIN_PATTERN.sub(replace_origin, text)


def _origin_key(parts: SplitResult) -> str:
    """生成仅用于内存反向索引的规范化 origin key。

    输入参数：
        parts：由 ``urlsplit`` 产生的 URL 分解结果。
    输出返回值：
        scheme 与 authority 小写化后的 origin 字符串；不含 path 和查询。
    """

    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), "", "", "")
    )


def _validate_origin(parts: SplitResult) -> None:
    """验证配置值是可安全拼接路径的纯 HTTP(S) origin。

    输入参数：
        parts：由 ``urlsplit`` 产生的部署地址分解结果。
    输出返回值：
        无；合法 origin 正常返回。
    异常：
        WebMallURLRegistryError：协议、主机、用户信息或附加 URL 部分不合规。
    """

    is_http = parts.scheme.lower() in {"http", "https"}
    has_host = parts.hostname is not None
    has_userinfo = parts.username is not None or parts.password is not None
    has_extra_parts = (
        parts.path not in {"", "/"} or bool(parts.query) or bool(parts.fragment)
    )
    if not is_http or not has_host or has_userinfo or has_extra_parts:
        raise WebMallURLRegistryError(
            "WebMall runtime 配置必须是无凭据和附加部分的 HTTP(S) origin"
        )
