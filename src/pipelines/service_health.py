"""
External service health checks used before dispatching tasks to agents.

The checks are intentionally content-aware. A port returning HTTP 200 is not
enough for WebMall: the default empty WordPress page also returns 200, so we
verify expected store titles and product availability before agent execution.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import warnings
from dataclasses import dataclass
from html import unescape
from typing import Iterable, List, Optional

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* or chardet .* doesn't match a supported version!",
)

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config_loader import DeployConfig


WEBMALL_EXPECTED_SHOPS = [
    ("E-Store Athletes", 9081),
    ("TechTalk", 9082),
    ("CamelCases", 9083),
    ("Hardware Cafe", 9084),
]


@dataclass
class ServiceCheckResult:
    service: str
    target: str
    ok: bool
    detail: str


class ServiceHealthError(RuntimeError):
    """Raised when a required external service is not ready."""

    def __init__(self, results: Iterable[ServiceCheckResult]):
        self.results = list(results)
        failed = [r for r in self.results if not r.ok]
        message = "; ".join(f"{r.service} {r.target}: {r.detail}" for r in failed)
        super().__init__(message or "external service health check failed")


def _title_from_html(body: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", body or "", flags=re.I | re.S)
    if not match:
        return ""
    return " ".join(unescape(match.group(1)).split())


def _get(url: str, timeout: float) -> requests.Response:
    return requests.get(url, timeout=timeout, allow_redirects=True)


def _result(service: str, target: str, ok: bool, detail: str) -> ServiceCheckResult:
    return ServiceCheckResult(service=service, target=target, ok=ok, detail=detail)


def check_webmall_health(
    deploy: Optional[DeployConfig] = None,
    *,
    timeout: float = 8.0,
    check_frontend: bool = True,
) -> List[ServiceCheckResult]:
    """Check WebMall frontend and all WooCommerce shops."""
    deploy = deploy or DeployConfig()
    raw = deploy.raw()
    host = deploy.webmall_host
    configured_ports = list(deploy.webmall_ports)
    expected_names = [name for name, _ in WEBMALL_EXPECTED_SHOPS]
    results: List[ServiceCheckResult] = []

    if check_frontend:
        frontend_port = int(raw.get("services", {}).get("webmall", {}).get("frontend_port", 8090))
        url = f"http://{host}:{frontend_port}/"
        try:
            resp = _get(url, timeout)
            title = _title_from_html(resp.text)
            ok = resp.status_code == 200 and "webmall" in title.lower()
            detail = f"HTTP {resp.status_code}, title={title or '<empty>'}"
        except Exception as exc:
            ok = False
            detail = f"request failed: {exc}"
        results.append(_result("webmall-frontend", url, ok, detail))

    for idx, port in enumerate(configured_ports[:4]):
        expected_name = expected_names[idx] if idx < len(expected_names) else ""
        base_url = f"http://{host}:{port}"

        try:
            resp = _get(base_url + "/", timeout)
            title = _title_from_html(resp.text)
            body_lower = (resp.text or "").lower()
            title_lower = title.lower()
            default_blog = title_lower in {"user's blog", "user’s blog", "wordpress"}
            maintenance_page = (
                "pardon our dust" in body_lower
                or "working on something amazing" in body_lower
            )
            title_matches = (not expected_name) or expected_name.lower() in title_lower
            ok = (
                resp.status_code == 200
                and bool(title)
                and title_matches
                and not default_blog
                and not maintenance_page
            )
            detail = f"HTTP {resp.status_code}, title={title or '<empty>'}"
            if default_blog:
                detail += " (default WordPress page)"
            if maintenance_page:
                detail += " (maintenance page)"
        except Exception as exc:
            ok = False
            detail = f"request failed: {exc}"
        results.append(_result("webmall-shop", base_url, ok, detail))

        api_url = f"{base_url}/wp-json/wp/v2/product?per_page=1"
        try:
            api_resp = _get(api_url, timeout)
            product_total = api_resp.headers.get("X-WP-Total", "")
            payload = api_resp.json()
            has_product = isinstance(payload, list) and len(payload) > 0
            ok = api_resp.status_code == 200 and has_product
            detail = f"HTTP {api_resp.status_code}, products={product_total or ('>=1' if has_product else '0')}"
        except Exception as exc:
            ok = False
            detail = f"product API failed: {exc}"
        results.append(_result("webmall-products", api_url, ok, detail))

    return results


def check_onlyoffice_health(
    deploy: Optional[DeployConfig] = None,
    *,
    timeout: float = 8.0,
) -> List[ServiceCheckResult]:
    """Check OnlyOffice document sharing service and DocumentServer API."""
    deploy = deploy or DeployConfig()
    raw = deploy.raw()
    host = deploy.onlyoffice_host
    flask_port = deploy.onlyoffice_flask_port
    doc_port = int(raw.get("services", {}).get("onlyoffice", {}).get("doc_server_port", 8080))
    results: List[ServiceCheckResult] = []

    share_url = f"http://{host}:{flask_port}"
    docs_url = f"{share_url}/api/documents"
    try:
        resp = _get(docs_url, timeout)
        payload = resp.json()
        documents = payload.get("documents") if isinstance(payload, dict) else None
        ok = resp.status_code == 200 and isinstance(documents, list)
        detail = f"HTTP {resp.status_code}, documents={len(documents) if isinstance(documents, list) else '?'}"
    except Exception as exc:
        ok = False
        detail = f"document sharing API failed: {exc}"
    results.append(_result("onlyoffice-share", docs_url, ok, detail))

    api_url = f"http://{host}:{doc_port}/web-apps/apps/api/documents/api.js"
    try:
        resp = _get(api_url, timeout)
        body = resp.text or ""
        ok = resp.status_code == 200 and ("DocsAPI" in body or "DocEditor" in body)
        detail = f"HTTP {resp.status_code}, api.js={'ok' if ok else 'unexpected'}"
    except Exception as exc:
        ok = False
        detail = f"DocumentServer API failed: {exc}"
    results.append(_result("onlyoffice-docserver", api_url, ok, detail))

    return results


def required_service_checks_for_pipeline(
    pipeline_name: str,
    deploy: Optional[DeployConfig] = None,
    *,
    timeout: float = 8.0,
) -> List[ServiceCheckResult]:
    """Return health checks required by a pipeline."""
    deploy = deploy or DeployConfig()
    if pipeline_name == "webmall":
        return check_webmall_health(deploy, timeout=timeout)
    if pipeline_name == "searchwrite":
        return check_onlyoffice_health(deploy, timeout=timeout)
    return []


def ensure_pipeline_services_healthy(
    pipeline_name: str,
    deploy: Optional[DeployConfig] = None,
    *,
    timeout: float = 8.0,
    log=None,
) -> List[ServiceCheckResult]:
    """Run required checks and raise ServiceHealthError on any failure."""
    results = required_service_checks_for_pipeline(pipeline_name, deploy, timeout=timeout)
    if not results:
        if log:
            log.info("[ServiceHealth] %s 不依赖外部 WebMall/OnlyOffice 服务，跳过", pipeline_name)
        return []

    if log:
        log.info("[ServiceHealth] 检查 %s 依赖服务...", pipeline_name)
        for item in results:
            status = "PASS" if item.ok else "FAIL"
            log.info("[ServiceHealth] %-4s %-22s %s | %s", status, item.service, item.target, item.detail)

    failed = [item for item in results if not item.ok]
    if failed:
        raise ServiceHealthError(results)
    return results


def _print_results(results: List[ServiceCheckResult]) -> int:
    for item in results:
        status = "PASS" if item.ok else "FAIL"
        print(f"[{status}] {item.service:22s} {item.target} | {item.detail}")
    return 0 if all(item.ok for item in results) else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check external service health")
    parser.add_argument(
        "--pipeline",
        action="append",
        choices=["webmall", "searchwrite", "qa", "operation", "webnavigate"],
        help="Pipeline to check. Can be passed multiple times.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args(argv)

    deploy = DeployConfig()
    pipelines = args.pipeline or ["webmall", "searchwrite"]
    all_results: List[ServiceCheckResult] = []
    for pipeline_name in pipelines:
        all_results.extend(required_service_checks_for_pipeline(pipeline_name, deploy, timeout=args.timeout))
    return _print_results(all_results)


if __name__ == "__main__":
    raise SystemExit(main())
