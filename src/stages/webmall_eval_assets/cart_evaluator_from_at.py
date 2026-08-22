#!/usr/bin/env python3
"""
基于 Accessibility Tree 的购物车评价器

功能：
- 从多个虚拟机获取购物车页面的 AT
- 从 AT 中提取商品链接（href）
- 与标准答案进行 slug 匹配
- 输出评价结果

使用方法:
    # 基本用法：指定虚拟机和期望的产品 URL
    python cart_evaluator_from_at.py --vm-ip <HOST_IP> --server-port 5000 \
        --expected "http://<HOST_IP>:9082/product/gamemax-iceburg-360mm-argb-liquid-cpu-cooler"

    # 多虚拟机、多产品
    python cart_evaluator_from_at.py --vm-ip <HOST_IP> --server-port 5000 5001 \
        --expected "http://shop1/product/product-a" "http://shop2/product/product-b"
    
    # 从配置文件读取
    python cart_evaluator_from_at.py --config cart_eval_config.json
"""

import requests
import json
import argparse
import time
import re
import html
from typing import List, Dict, Any, Optional, Tuple, Set
from urllib.parse import urlparse
from dataclasses import dataclass, field

try:
    from webmall_identity import (
        deep_html_unescape,
        product_identity_matches,
        slugify_product_name,
    )
except ImportError:  # 作为 webmall_eval_assets 包导入时
    from webmall_eval_assets.webmall_identity import (
        deep_html_unescape,
        product_identity_matches,
        slugify_product_name,
    )


# 4 个商店的端口和名称
SHOPS = [
    {"port": 9081, "name": "E-Store Athletes", "url_key": "URL_1"},
    {"port": 9082, "name": "TechTalk", "url_key": "URL_2"},
    {"port": 9083, "name": "CamelCases", "url_key": "URL_3"},
    {"port": 9084, "name": "Hardware Cafe", "url_key": "URL_4"},
]


@dataclass
class Checkpoint:
    """
    检查点，表示一个期望的商品
    
    属性:
        id: 检查点ID
        value: 期望的产品URL
        slug: 从URL提取的产品标识符
        domain: 产品所在的域名（含端口）
        flag: 是否已匹配
        weight: 权重
    """
    id: str
    value: str
    slug: str = ""
    domain: str = ""
    flag: bool = False
    weight: float = 1.0
    
    def __post_init__(self):
        """初始化后自动提取 slug 和 domain"""
        if not self.slug:
            self.slug = self._extract_slug(self.value)
        if not self.domain:
            self.domain = self._extract_domain(self.value)
    
    @staticmethod
    def _extract_slug(url: str) -> str:
        """从 URL 中提取产品 slug"""
        return urlparse(url).path.rstrip("/").split("/")[-1]
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 中提取域名（含端口）"""
        return urlparse(url).netloc


@dataclass
class CartDetectionResult:
    """
    单个商店的购物车检测结果
    
    属性:
        shop_name: 商店名称
        shop_port: 商店端口
        url_key: URL键（如 URL_1）
        detected_url: 检测到的当前页面URL
        is_cart_page: 是否是购物车页面
        products: 检测到的商品列表
        product_slugs: 检测到的商品slug集合
        product_hrefs: 检测到的商品链接集合
        cart_is_empty: 购物车是否为空
        error: 错误信息
    """
    shop_name: str
    shop_port: int
    url_key: str
    detected_url: str = ""
    is_cart_page: bool = False
    products: List[Dict] = field(default_factory=list)
    product_slugs: Set[str] = field(default_factory=set)
    product_hrefs: Set[str] = field(default_factory=set)
    cart_is_empty: bool = False
    error: str = None


@dataclass
class EvaluationResult:
    """
    评价结果
    
    属性:
        vm_key: 虚拟机标识
        shop_results: 各商店的检测结果
        matched_checkpoints: 匹配的检查点列表
        unmatched_checkpoints: 未匹配的检查点列表
        unexpected_products: 意外的商品（不在期望列表中）
        score: 总分
        total_weight: 总权重
    """
    vm_key: str
    shop_results: List[CartDetectionResult] = field(default_factory=list)
    matched_checkpoints: List[Checkpoint] = field(default_factory=list)
    unmatched_checkpoints: List[Checkpoint] = field(default_factory=list)
    unexpected_products: List[Dict] = field(default_factory=list)
    score: float = 0.0
    total_weight: float = 0.0


def get_accessibility_tree(vm_ip: str, server_port: int) -> str:
    """
    从虚拟机获取 Accessibility Tree
    
    参数:
        vm_ip: 虚拟机 IP 地址
        server_port: Python server 端口
    
    返回:
        str: Accessibility Tree XML 字符串
    """
    api_url = f"http://{vm_ip}:{server_port}/accessibility"
    
    try:
        response = requests.get(api_url, timeout=30)
        if response.status_code == 200:
            try:
                data = response.json()
                return data.get("AT", response.text)
            except:
                return response.text
        else:
            return None
    except Exception as e:
        print(f"    获取 AT 失败: {e}")
        return None


def open_url_in_vm(vm_ip: str, server_port: int, url: str) -> bool:
    """
    在虚拟机上打开指定 URL
    
    参数:
        vm_ip: 虚拟机 IP 地址
        server_port: Python server 端口
        url: 要打开的 URL
    
    返回:
        bool: 是否成功
    """
    headers = {"Content-Type": "application/json"}
    api_url = f"http://{vm_ip}:{server_port}/execute"
    # 通过 Python 子进程显式设置 DISPLAY 并检查 xdg-open 返回码。这比仅
    # 判断 /execute HTTP 200 更能区分“命令被接收”与“浏览器导航成功”。
    script = (
        "import os, subprocess\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        f"result = subprocess.run(['xdg-open', {url!r}], env=env, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print('OPEN_OK' if result.returncode == 0 else 'OPEN_FAIL')\n"
    )
    payload = json.dumps({
        "command": ["python3", "-c", script],
        "shell": False,
    })
    
    try:
        response = requests.post(api_url, headers=headers, data=payload, timeout=30)
        if response.status_code != 200:
            return False
        try:
            output = str(response.json().get("output") or "")
        except Exception:
            output = response.text
        return "OPEN_OK" in output
    except Exception as e:
        print(f"    打开 URL 失败: {e}")
        return False


def _deep_unescape(text: str) -> str:
    """
    循环解码 HTML 实体直到稳定

    AT 中的文本可能经过多层 HTML 编码（如 &amp;amp;amp; → &amp;amp; → &amp; → &），
    单次 html.unescape 只能解码一层，需要循环调用直到结果不再变化。

    参数:
        text: 可能含多层 HTML 实体编码的文本

    返回:
        完全解码后的文本
    """
    return deep_html_unescape(text)


def name_to_slug(name: str) -> str:
    """
    将商品名称转换为 slug 格式（与 WooCommerce 的 sanitize_title 行为一致）

    例如: "GameMax Iceburg 360mm ARGB Liquid CPU Cooler, 12cm ARGB PWM Fans"
    转换为: "gamemax-iceburg-360mm-argb-liquid-cpu-cooler-12cm-argb-pwm-fans"

    例如: "P12 PWM PST Fans & PWM Controlled Pump"
    转换为: "p12-pwm-pst-fans-amp-pwm-controlled-pump"

    参数:
        name: 商品名称（应先经过 html.unescape 解码）

    返回:
        slug 格式的字符串
    """
    return slugify_product_name(name, keep_amp=True)


def _is_cart_url_for_shop(url: str, shop_port: Optional[int]) -> bool:
    """判断当前 URL 是否为指定 WebMall 店铺的购物车页。

    功能：分别校验 URL path 与端口，不使用整串 ``/cart`` 子串或页面文本
    作为已进入购物车的充分证据。
    输入参数：url 为 AT 地址栏 URL；shop_port 为当前正在评价的店铺端口。
    输出返回值：URL 为该店铺的精确 cart 路径时返回 True。
    """
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    if not re.fullmatch(r"/cart/?", parsed.path or "", flags=re.IGNORECASE):
        return False
    if shop_port is not None and parsed.port != shop_port:
        return False
    return True


def extract_product_hrefs_from_at(at_xml: str) -> Tuple[Set[str], Set[str], List[Dict]]:
    """
    从 Accessibility Tree 中提取商品链接
    
    参数:
        at_xml: Accessibility Tree XML 字符串
        
    返回:
        (商品链接集合, 商品slug集合, 商品详情列表)
    """
    hrefs = set()
    slugs = set()
    products = []
    
    # 方法1: 查找包含 /product/ 的链接
    # AT 格式可能是: <entry role="link" name="Product Name" href="http://...">
    href_patterns = [
        # 标准 href 属性
        r'href="([^"]*?/product/[^"]+)"',
        # AT XML 格式中的链接
        r'<entry[^>]*role="link"[^>]*>[^<]*href="([^"]*?/product/[^"]+)"',
        # 购物车行中的产品链接
        r'<a[^>]*href="([^"]*?/product/[^"]+)"[^>]*>',
    ]
    
    for pattern in href_patterns:
        matches = re.findall(pattern, at_xml, re.IGNORECASE)
        for href in matches:
            # 循环解码 HTML 实体（AT 中 & 可能被多层编码为 &amp;amp;amp; 等）
            href = _deep_unescape(href)
            if "/product/" in href:
                hrefs.add(href)
                # 提取 slug
                slug = urlparse(href).path.rstrip("/").split("/")[-1]
                if slug:
                    slugs.add(slug)
    
    # 方法2: 从 "Remove XXX from cart" 模式中提取商品名称
    remove_pattern = r'name="Remove ([^"]+) from cart'
    remove_matches = re.findall(remove_pattern, at_xml)
    
    for name in remove_matches:
        # 循环解码 HTML 实体后清理名称（AT 中 & 可能被多层编码）
        clean_name = _deep_unescape(name).strip()
        
        # 将商品名称转换为 slug 格式
        generated_slug = name_to_slug(clean_name)
        
        product_info = {
            "name": clean_name,
            "href": None,
            "slug": generated_slug,  # 使用从名称生成的 slug
            "source": "remove_pattern"
        }
        
        # 添加生成的 slug 到集合中
        if generated_slug:
            slugs.add(generated_slug)
        
        # 尝试查找这个商品对应的链接（如果 AT 中有的话）
        name_escaped = re.escape(clean_name[:50])
        link_pattern = rf'name="{name_escaped}[^"]*"[^>]*href="([^"]+)"'
        link_match = re.search(link_pattern, at_xml)
        
        if link_match:
            href = link_match.group(1)
            if "/product/" in href:
                product_info["href"] = href
                # 如果找到了真实的链接，使用链接中的 slug（更准确）
                real_slug = urlparse(href).path.rstrip("/").split("/")[-1]
                if real_slug:
                    product_info["slug"] = real_slug
                    slugs.add(real_slug)
                hrefs.add(href)
        
        products.append(product_info)
    
    # 方法3: 查找 "Quantity of XXX in your cart" 模式
    qty_pattern = r'name="Quantity of ([^"]+) in your cart'
    qty_matches = re.findall(qty_pattern, at_xml)
    
    for name in qty_matches:
        # 循环解码 HTML 实体后清理名称
        clean_name = _deep_unescape(name).strip()
        
        # 检查是否已经在列表中
        exists = any(p["name"] == clean_name for p in products)
        if not exists:
            # 将商品名称转换为 slug 格式
            generated_slug = name_to_slug(clean_name)
            
            product_info = {
                "name": clean_name,
                "href": None,
                "slug": generated_slug,
                "source": "quantity_pattern"
            }
            
            if generated_slug:
                slugs.add(generated_slug)
            
            products.append(product_info)
    
    return hrefs, slugs, products


def extract_current_url_from_at(at_xml: str) -> str:
    """
    从 AT 中提取当前页面 URL
    
    参数:
        at_xml: Accessibility Tree XML
        
    返回:
        当前页面 URL
    """
    url_patterns = [
        r'<entry[^>]*name="Address and search bar"[^>]*>([^<]+)</entry>',
        r'name="Address and search bar"[^>]*>([^<]+)<',
    ]
    
    for pattern in url_patterns:
        match = re.search(pattern, at_xml)
        if match:
            url_text = match.group(1).strip()
            if url_text and not url_text.startswith("http"):
                url_text = f"http://{url_text}"
            return url_text
    
    return ""


def detect_cart_from_at(at_xml: str, shop_port: int = None) -> CartDetectionResult:
    """
    从 AT 中检测购物车信息
    
    参数:
        at_xml: Accessibility Tree XML
        shop_port: 商店端口（用于过滤链接）
        
    返回:
        CartDetectionResult: 检测结果
    """
    result = CartDetectionResult(
        shop_name="",
        shop_port=shop_port or 0,
        url_key=""
    )
    
    # 提取当前 URL
    result.detected_url = extract_current_url_from_at(at_xml)
    
    # 购物车状态必须由地址栏中的正确店铺 cart URL 证明。
    result.is_cart_page = _is_cart_url_for_shop(result.detected_url, shop_port)
    if not result.is_cart_page:
        result.error = "当前页面不是指定店铺的购物车页"
        return result
    
    # 检查购物车是否为空
    if "cart is currently empty" in at_xml.lower():
        result.cart_is_empty = True
        return result
    
    # 提取商品链接
    hrefs, slugs, products = extract_product_hrefs_from_at(at_xml)
    
    # 如果指定了商店端口，实体链接必须属于该端口。仅有 Remove/
    # Quantity 文本时则可使用当前已验证 cart 页中生成的 slug。
    if shop_port:
        filtered_hrefs = set()
        filtered_slugs = {
            str(product.get("slug") or "")
            for product in products
            if product.get("slug") and not product.get("href")
        }
        current_host = (urlparse(result.detected_url).hostname or "").lower()
        for href in hrefs:
            try:
                parsed_href = urlparse(href)
            except (TypeError, ValueError):
                continue
            if parsed_href.port != shop_port:
                continue
            if current_host and (parsed_href.hostname or "").lower() != current_host:
                continue
            filtered_hrefs.add(href)
            slug = parsed_href.path.rstrip("/").split("/")[-1]
            if slug:
                filtered_slugs.add(slug)
        hrefs = filtered_hrefs
        slugs = filtered_slugs
    
    result.product_hrefs = hrefs
    result.product_slugs = slugs
    result.products = products
    
    return result


def evaluate_cart(
    detection_result: CartDetectionResult,
    checkpoints: List[Checkpoint],
    current_domain: str = None
) -> Tuple[float, List[Checkpoint], List[Checkpoint], List[str]]:
    """
    评价购物车检测结果
    
    参数:
        detection_result: 购物车检测结果
        checkpoints: 检查点列表
        current_domain: 当前域名（用于匹配）
        
    返回:
        (得分, 匹配的检查点, 未匹配的检查点, 意外的商品)
    """
    score = 0.0
    matched = []
    unmatched = []
    unexpected = []
    
    detected_slugs = detection_result.product_slugs.copy()
    
    for cp in checkpoints:
        # 检查域名是否匹配（如果指定了当前域名）
        if current_domain and cp.domain != current_domain:
            continue
        
        # 检查 slug 是否在检测到的商品中
        if cp.slug in detected_slugs:
            if not cp.flag:
                cp.flag = True
                score += cp.weight
                matched.append(cp)
                detected_slugs.discard(cp.slug)  # 移除已匹配的
        else:
            unmatched.append(cp)
    
    # 剩余的 slug 是意外的商品
    unexpected = list(detected_slugs)
    
    return score, matched, unmatched, unexpected


def detect_vm_all_carts(
    vm_ip: str, 
    server_port: int, 
    shop_ip: str, 
    wait_time: float = 3.0
) -> List[CartDetectionResult]:
    """
    检测单个虚拟机上所有商店的购物车
    
    参数:
        vm_ip: 虚拟机 IP 地址
        server_port: Python server 端口
        shop_ip: 商店服务器 IP
        wait_time: 打开页面后等待的秒数
        
    返回:
        所有商店的购物车检测结果列表
    """
    results = []
    
    for shop in SHOPS:
        cart_url = f"http://{shop_ip}:{shop['port']}/cart/"
        print(f"\n  📦 {shop['name']} ({shop['url_key']})")
        print(f"     打开: {cart_url}")
        
        # 打开购物车页面
        success = open_url_in_vm(vm_ip, server_port, cart_url)
        if not success:
            result = CartDetectionResult(
                shop_name=shop["name"],
                shop_port=shop["port"],
                url_key=shop["url_key"],
                error="无法打开购物车页面"
            )
            results.append(result)
            continue
        
        # 等待页面加载
        print(f"     等待 {wait_time} 秒...")
        time.sleep(wait_time)
        
        # 获取 AT 并检测
        at_xml = get_accessibility_tree(vm_ip, server_port)
        if not at_xml:
            result = CartDetectionResult(
                shop_name=shop["name"],
                shop_port=shop["port"],
                url_key=shop["url_key"],
                error="无法获取 Accessibility Tree"
            )
            results.append(result)
            continue
        
        # 检测购物车
        result = detect_cart_from_at(at_xml, shop["port"])
        result.shop_name = shop["name"]
        result.shop_port = shop["port"]
        result.url_key = shop["url_key"]
        
        # 打印结果
        if result.cart_is_empty:
            print(f"     🛒 购物车为空")
        elif result.product_slugs:
            print(f"     🛒 检测到 {len(result.product_slugs)} 个商品 slug:")
            for slug in result.product_slugs:
                print(f"        ✓ {slug}")
            if result.product_hrefs:
                print(f"     🔗 商品链接:")
                for href in result.product_hrefs:
                    print(f"        - {href}")
        elif result.products:
            print(f"     🛒 检测到 {len(result.products)} 个商品（无链接）:")
            for p in result.products:
                print(f"        - {p['name']}")
        else:
            if result.is_cart_page:
                print(f"     ⚠️ 购物车状态未知")
            else:
                print(f"     ⚠️ 当前页面可能不是购物车页面")
        
        results.append(result)
    
    return results


def evaluate_all_vms(
    all_results: Dict[str, List[CartDetectionResult]],
    checkpoints: List[Checkpoint]
) -> Dict[str, EvaluationResult]:
    """
    逐 VM 独立评价购物车，并投影最佳单 VM 的完成度。

    功能:
        每台 VM 使用独立匹配状态生成 matched/unmatched/score，禁止把
        不同 VM 的互补商品合并为完整购物车。所有 VM 完成评价后，只将
        命中检查点数量最多的一台 VM 投影回输入 ``Checkpoint.flag``；
        同分时保持 ``all_results`` 输入顺序。若同一期望商品出现在多台
        VM，第二台及后续 VM 仍记录为重复外部副作用。
    输入参数:
        all_results: 所有虚拟机的检测结果 {vm_key: [CartDetectionResult, ...]}
        checkpoints: 检查点列表
    输出返回值:
        评价结果字典
    """

    evaluation_results: Dict[str, EvaluationResult] = {}
    matched_details_by_vm: Dict[str, Dict[str, Dict[str, Any]]] = {}

    # 调用方可能复用 Checkpoint 实例；进入本轮评价时必须清除旧投影。
    for checkpoint in checkpoints:
        checkpoint.flag = False

    for vm_key, shop_results in all_results.items():
        eval_result = EvaluationResult(vm_key=vm_key)
        eval_result.shop_results = shop_results
        matched_details_by_vm[vm_key] = {}

        # 构建 商店端口 -> 检测到的 slug 集合 的映射
        port_to_slugs: Dict[int, Set[str]] = {}

        for shop_result in shop_results:
            if shop_result.error or not shop_result.is_cart_page:
                continue
            port = shop_result.shop_port
            if port not in port_to_slugs:
                port_to_slugs[port] = set()
            port_to_slugs[port].update(shop_result.product_slugs)

        # 每台 VM 独立评价全部检查点；不得读取或写入共享 cp.flag。
        for cp in checkpoints:
            # 从检查点的域名中提取端口
            cp_port = int(cp.domain.split(":")[-1]) if ":" in cp.domain else 0

            # 检查该端口的商店是否检测到了对应的 slug
            matched = False
            matched_detected_slug = ""

            if cp_port in port_to_slugs:
                # 仅对 amp 词元的 WooCommerce 差异容忍，其余词元和数字
                # 型号必须完整一致。
                for detected_slug in list(port_to_slugs[cp_port]):
                    if product_identity_matches(cp.slug, detected_slug):
                        matched = True
                        matched_detected_slug = detected_slug
                        port_to_slugs[cp_port].discard(detected_slug)
                        break

            if matched:
                eval_result.score += cp.weight
                eval_result.matched_checkpoints.append(cp)
                matched_details_by_vm[vm_key][cp.id] = {
                    "shop_port": cp_port,
                    "slug": matched_detected_slug,
                }
            else:
                eval_result.unmatched_checkpoints.append(cp)

        # 计算总权重
        eval_result.total_weight = sum(cp.weight for cp in checkpoints)

        # 找出意外的商品
        expected_pairs = []
        for cp in checkpoints:
            cp_port = int(cp.domain.rsplit(":", 1)[-1]) if ":" in cp.domain else 0
            expected_pairs.append((cp_port, cp.slug))
        for shop_result in shop_results:
            if shop_result.error or not shop_result.is_cart_page:
                continue
            for slug in shop_result.product_slugs:
                expected_here = [
                    expected_slug
                    for port, expected_slug in expected_pairs
                    if port == shop_result.shop_port
                ]
                if not any(product_identity_matches(item, slug) for item in expected_here):
                    eval_result.unexpected_products.append({
                        "shop_port": shop_result.shop_port,
                        "slug": slug,
                    })

        evaluation_results[vm_key] = eval_result

    # 保留“同一期望商品被多个 VM 重复加购”的既有严格副作用语义。
    first_vm_by_checkpoint: Dict[str, str] = {}
    for vm_key in all_results:
        for checkpoint_id, detail in matched_details_by_vm[vm_key].items():
            if checkpoint_id not in first_vm_by_checkpoint:
                first_vm_by_checkpoint[checkpoint_id] = vm_key
                continue
            evaluation_results[vm_key].unexpected_products.append({
                "shop_port": detail["shop_port"],
                "slug": detail["slug"],
                "reason": "duplicate_expected_product_across_vms",
            })

    # 兼容现有 runner：它们通过输入 checkpoint.flag 计算总体 recall。
    # 这里只投影最佳单 VM 的匹配集合，绝不对 VM 结果求并集。
    if evaluation_results:
        best_result = max(
            evaluation_results.values(),
            key=lambda result: len(result.matched_checkpoints),
        )
        best_checkpoint_ids = {
            checkpoint.id for checkpoint in best_result.matched_checkpoints
        }
        for checkpoint in checkpoints:
            checkpoint.flag = checkpoint.id in best_checkpoint_ids

    return evaluation_results


def create_checkpoints_from_urls(urls: List[str]) -> List[Checkpoint]:
    """
    从 URL 列表创建检查点
    
    参数:
        urls: 产品 URL 列表
        
    返回:
        检查点列表
    """
    checkpoints = []
    weight = 1.0 / len(urls) if urls else 0
    
    for i, url in enumerate(urls, 1):
        cp = Checkpoint(
            id=f"cart_{i}",
            value=url,
            weight=weight
        )
        checkpoints.append(cp)
    
    return checkpoints


def print_evaluation_results(eval_results: Dict[str, EvaluationResult], checkpoints: List[Checkpoint]):
    """
    打印评价结果
    
    参数:
        eval_results: 评价结果字典
        checkpoints: 检查点列表
    """
    print("\n" + "=" * 70)
    print("📊 购物车评价结果")
    print("=" * 70)
    
    # 打印期望的商品
    print("\n📋 期望的商品:")
    for cp in checkpoints:
        status = "✓" if cp.flag else "✗"
        print(f"  {status} [{cp.id}] {cp.slug}")
        print(f"      URL: {cp.value}")
        print(f"      域名: {cp.domain}")
    
    # 打印每个虚拟机的结果
    for vm_key, result in eval_results.items():
        print(f"\n{'─' * 70}")
        print(f"🖥️  虚拟机: {vm_key}")
        print(f"{'─' * 70}")
        
        # 各商店检测情况
        print("\n  商店检测情况:")
        for shop_result in result.shop_results:
            if shop_result.error:
                print(f"    ❌ {shop_result.shop_name}: {shop_result.error}")
            elif shop_result.cart_is_empty:
                print(f"    🛒 {shop_result.shop_name}: 空")
            elif shop_result.product_slugs:
                print(f"    🛒 {shop_result.shop_name}: {len(shop_result.product_slugs)} 个商品")
                for slug in shop_result.product_slugs:
                    print(f"        - {slug}")
            else:
                print(f"    ⚠️ {shop_result.shop_name}: 状态未知")
        
        # 匹配结果
        print(f"\n  匹配结果:")
        print(f"    ✓ 匹配: {len(result.matched_checkpoints)}/{len(checkpoints)}")
        for cp in result.matched_checkpoints:
            print(f"        - {cp.slug} ({cp.domain})")
        
        if result.unmatched_checkpoints:
            print(f"    ✗ 未匹配: {len(result.unmatched_checkpoints)}")
            for cp in result.unmatched_checkpoints:
                print(f"        - {cp.slug} ({cp.domain})")
        
        if result.unexpected_products:
            print(f"    ⚠️ 意外商品: {len(result.unexpected_products)}")
            for p in result.unexpected_products:
                print(f"        - {p['slug']}")
        
        # 得分
        print(f"\n  📈 得分: {result.score:.2f} / {result.total_weight:.2f}")
        if result.total_weight > 0:
            print(f"     百分比: {result.score / result.total_weight * 100:.1f}%")
    
    # 总体结果
    print("\n" + "=" * 70)
    print("📈 总体评价")
    print("=" * 70)
    
    total_matched = sum(1 for cp in checkpoints if cp.flag)
    total_expected = len(checkpoints)
    
    print(f"  期望商品: {total_expected}")
    print(f"  已匹配: {total_matched}")
    print(f"  匹配率: {total_matched / total_expected * 100:.1f}%" if total_expected > 0 else "  匹配率: N/A")


def main():
    parser = argparse.ArgumentParser(
        description="基于 AT 的购物车评价器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python cart_evaluator_from_at.py --vm-ip <HOST_IP> --server-port 5000 \\
      --expected "http://<HOST_IP>:9082/product/gamemax-iceburg-360mm"

  # 多虚拟机
  python cart_evaluator_from_at.py --vm-ip <HOST_IP> --server-port 5000 5001 \\
      --expected "http://shop/product/product-a" "http://shop/product/product-b"
  
  # 从配置文件
  python cart_evaluator_from_at.py --config cart_eval_config.json
        """
    )
    
    parser.add_argument("--vm-ip", default="127.0.0.1",
                        help="虚拟机 IP（默认 127.0.0.1；跨机部署时显式传入）")
    parser.add_argument("--server-port", type=int, nargs="+", default=[5000], 
                        help="Python server 端口列表")
    parser.add_argument("--shop-ip", default=None, 
                        help="商店服务器 IP (默认与 vm-ip 相同)")
    parser.add_argument("--wait-time", type=float, default=3.0,
                        help="打开页面后等待的秒数 (默认 3.0)")
    parser.add_argument("--expected", nargs="+", default=[],
                        help="期望的产品 URL 列表")
    parser.add_argument("--config", help="配置文件路径 (JSON)")
    parser.add_argument("--output", "-o", help="输出结果到文件 (JSON)")
    
    args = parser.parse_args()
    
    # 从配置文件读取
    expected_products = args.expected
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        expected_products = config.get("expected_products", [])
        if "vm_ip" in config:
            args.vm_ip = config["vm_ip"]
        if "server_ports" in config:
            args.server_port = config["server_ports"]
        if "shop_ip" in config:
            args.shop_ip = config["shop_ip"]
    
    shop_ip = args.shop_ip or args.vm_ip
    
    # 创建检查点
    checkpoints = create_checkpoints_from_urls(expected_products)
    
    print("=" * 70)
    print("基于 AT 的购物车评价器")
    print("=" * 70)
    print(f"虚拟机 IP: {args.vm_ip}")
    print(f"商店 IP: {shop_ip}")
    print(f"Server 端口: {args.server_port}")
    print(f"等待时间: {args.wait_time} 秒")
    print(f"\n期望的产品 ({len(expected_products)} 个):")
    for i, url in enumerate(expected_products, 1):
        cp = checkpoints[i-1] if i <= len(checkpoints) else None
        slug = cp.slug if cp else "N/A"
        print(f"  {i}. {slug}")
        print(f"     {url}")
    print("=" * 70)
    
    # 检测所有虚拟机的购物车
    all_results = {}
    
    for port in args.server_port:
        print(f"\n{'='*70}")
        print(f"🖥️  虚拟机 {args.vm_ip}:{port}")
        print("=" * 70)
        
        results = detect_vm_all_carts(args.vm_ip, port, shop_ip, args.wait_time)
        all_results[f"{args.vm_ip}:{port}"] = results
    
    # 评价
    if checkpoints:
        eval_results = evaluate_all_vms(all_results, checkpoints)
        print_evaluation_results(eval_results, checkpoints)
        
        # 输出 JSON
        if args.output:
            output_data = {
                "checkpoints": [
                    {
                        "id": cp.id,
                        "value": cp.value,
                        "slug": cp.slug,
                        "domain": cp.domain,
                        "flag": cp.flag,
                        "weight": cp.weight
                    }
                    for cp in checkpoints
                ],
                "evaluation_results": {
                    vm_key: {
                        "score": result.score,
                        "total_weight": result.total_weight,
                        "matched_count": len(result.matched_checkpoints),
                        "unmatched_count": len(result.unmatched_checkpoints),
                        "matched_slugs": [cp.slug for cp in result.matched_checkpoints],
                        "unmatched_slugs": [cp.slug for cp in result.unmatched_checkpoints],
                        "unexpected_products": result.unexpected_products
                    }
                    for vm_key, result in eval_results.items()
                }
            }
            
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            print(f"\n结果已保存到: {args.output}")
    else:
        # 只打印检测结果
        print("\n" + "=" * 70)
        print("📊 检测结果汇总（未指定期望产品）")
        print("=" * 70)
        
        for vm_key, results in all_results.items():
            print(f"\n【{vm_key}】")
            for r in results:
                if r.error:
                    print(f"  ❌ {r.shop_name}: {r.error}")
                elif r.cart_is_empty:
                    print(f"  🛒 {r.shop_name}: 空")
                elif r.product_slugs:
                    print(f"  🛒 {r.shop_name}: {len(r.product_slugs)} 个商品")
                    for slug in r.product_slugs:
                        print(f"      - {slug}")
                else:
                    print(f"  ⚠️ {r.shop_name}: 状态未知")
    
    # 输出 JSON 格式的检测结果
    print("\n" + "=" * 70)
    print("📄 JSON 格式检测结果")
    print("=" * 70)
    
    json_results = {}
    for vm_key, results in all_results.items():
        json_results[vm_key] = [
            {
                "shop": r.shop_name,
                "port": r.shop_port,
                "url_key": r.url_key,
                "is_cart_page": r.is_cart_page,
                "cart_is_empty": r.cart_is_empty,
                "product_slugs": list(r.product_slugs),
                "product_hrefs": list(r.product_hrefs),
                "products": r.products,
                "error": r.error
            }
            for r in results
        ]
    
    print(json.dumps(json_results, ensure_ascii=False, indent=2))
    
    return 0


if __name__ == "__main__":
    exit(main())


# 以下汇总函数自 2026-07-21 WebMall 恢复线补回：跨 VM 合并后按唯一 slug 计算
# unexpected，避免同一多余商品在 n>1 时被重复计入 precision 分母。
# 由 tests/test_webmall_cart_evaluator.py 断言。


def summarize_cart_evaluation(
    checkpoints: List[Checkpoint],
    evaluation_results: Dict[str, EvaluationResult],
) -> Dict[str, Any]:
    """
    汇总多 VM cart 评价结果。

    checkpoint.flag 表示跨 VM 合并后的唯一期望商品匹配；unexpected 也应按
    合并后的唯一 slug 计算，避免 n>1 时同一多余商品被重复计入 precision 分母。
    """
    matched_count = sum(1 for cp in checkpoints if cp.flag)
    total_expected = len(checkpoints)
    unexpected_slugs = sorted({
        product.get("slug", "")
        for result in evaluation_results.values()
        for product in result.unexpected_products
        if product.get("slug")
    })
    total_unexpected = len(unexpected_slugs)

    passed = (
        matched_count == total_expected and total_unexpected == 0
    ) if total_expected else False
    recall = matched_count / total_expected if total_expected else 0.0
    total_detected = matched_count + total_unexpected
    precision = (
        matched_count / total_detected
        if total_detected > 0
        else (1.0 if matched_count == 0 else 0.0)
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 else 0.0
    )
    score = matched_count / total_expected if total_expected > 0 else 0.0

    return {
        "score": score,
        "matched_count": matched_count,
        "max_score": total_expected,
        "passed": passed,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "total_unexpected": total_unexpected,
        "unexpected_slugs": unexpected_slugs,
    }


