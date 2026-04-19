#!/usr/bin/env python3
"""
虚拟机购物车评价器

基于 desktop_env 的 chrome.py 获取虚拟机上的购物车页面内容，
然后使用 CartEvaluator 逻辑判断是否成功添加购物车。

使用方法:
    python vm_cart_evaluator.py --vm-ip 10.1.110.114 --cart-url http://localhost:8081/cart

依赖:
    - 虚拟机上 Chrome 已启动并开启远程调试端口
    - 虚拟机上 Python server 正在运行
"""

import sys
import os
import json
import argparse
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# 添加 desktop_env 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from desktop_env.evaluators.getters.chrome import (
        get_page_info, 
        get_active_tab_info,
        get_open_tabs_info
    )
    HAS_CHROME_GETTER = True
except ImportError:
    HAS_CHROME_GETTER = False
    print("警告: 无法导入 chrome.py，将使用独立实现")

from playwright.sync_api import sync_playwright
import requests
import time
import platform


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class Checkpoint:
    """检查点"""
    id: str
    value: str  # 期望的产品 URL
    type: str   # cart, checkout, string, url
    flag: bool = False
    weight: float = 1.0


@dataclass 
class VMConfig:
    """虚拟机配置"""
    name: str
    vm_ip: str
    chromium_port: int = 9222
    server_port: int = 5000
    cart_urls: List[str] = field(default_factory=list)


@dataclass
class CartResult:
    """购物车检测结果"""
    vm_name: str
    cart_url: str
    actual_url: str = ""
    page_title: str = ""
    detected_products: List[Dict] = field(default_factory=list)
    matched_checkpoints: List[str] = field(default_factory=list)
    score: float = 0.0
    error: Optional[str] = None


# ============================================================================
# 模拟 env 对象（用于兼容 chrome.py 的函数）
# ============================================================================

class MockEnv:
    """
    模拟 desktop_env 的 env 对象
    用于调用 chrome.py 中的函数
    """
    def __init__(self, vm_ip: str, chromium_port: int = 9222, server_port: int = 5000):
        self.vm_ip = vm_ip
        self.chromium_port = chromium_port
        self.server_port = str(server_port)
        self.vm_platform = "Linux"


# ============================================================================
# 页面内容获取器
# ============================================================================

class VMPageFetcher:
    """
    从虚拟机获取页面内容
    
    支持两种方式：
    1. 使用 desktop_env 的 chrome.py 函数（如果可用）
    2. 直接使用 Playwright CDP 连接
    """
    
    def __init__(self, vm_ip: str, chromium_port: int = 9222, server_port: int = 5000):
        """
        初始化
        
        参数:
            vm_ip: 虚拟机 IP 地址
            chromium_port: Chrome 远程调试端口
            server_port: Python server 端口
        """
        self.vm_ip = vm_ip
        self.chromium_port = chromium_port
        self.server_port = server_port
        self.env = MockEnv(vm_ip, chromium_port, server_port)
    
    def get_page_content(self, url: str) -> Dict[str, Any]:
        """
        获取指定 URL 的页面内容
        
        参数:
            url: 要获取的页面 URL
            
        返回:
            {"title": ..., "url": ..., "content": ...} 或 {"error": ...}
        """
        if HAS_CHROME_GETTER:
            return self._get_via_chrome_getter(url)
        else:
            return self._get_via_playwright(url)
    
    def _get_via_chrome_getter(self, url: str) -> Dict[str, Any]:
        """使用 chrome.py 的函数获取页面"""
        try:
            config = {"url": url}
            result = get_page_info(self.env, config)
            if result:
                return {
                    "title": result.get("title", ""),
                    "url": result.get("url", url),
                    "content": result.get("content", "")
                }
            else:
                return {"error": "get_page_info returned None"}
        except Exception as e:
            return {"error": f"chrome.py error: {str(e)}"}
    
    def _get_via_playwright(self, url: str) -> Dict[str, Any]:
        """直接使用 Playwright CDP 连接获取页面"""
        remote_debugging_url = f"http://{self.vm_ip}:{self.chromium_port}"
        
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.connect_over_cdp(remote_debugging_url)
                except Exception as e:
                    # 尝试启动 Chrome
                    self._launch_chrome_with_debug()
                    time.sleep(3)
                    browser = p.chromium.connect_over_cdp(remote_debugging_url)
                
                # 获取或创建页面
                if browser.contexts and browser.contexts[0].pages:
                    page = browser.contexts[0].pages[0]
                else:
                    page = browser.contexts[0].new_page()
                
                # 导航到目标 URL
                page.goto(url, timeout=30000)
                page.wait_for_load_state('load')
                
                result = {
                    "title": page.title(),
                    "url": page.url,
                    "content": page.content()
                }
                
                browser.close()
                return result
                
        except Exception as e:
            return {"error": f"Playwright error: {str(e)}"}
    
    def _launch_chrome_with_debug(self):
        """通过 server 启动 Chrome（带远程调试端口）"""
        try:
            app = 'chromium' if 'arm' in platform.machine() else 'google-chrome'
            payload = json.dumps({
                "command": [app, f"--remote-debugging-port={self.chromium_port}"],
                "shell": False
            })
            headers = {"Content-Type": "application/json"}
            requests.post(
                f"http://{self.vm_ip}:{self.server_port}/setup/launch",
                headers=headers,
                data=payload,
                timeout=10
            )
        except Exception as e:
            print(f"启动 Chrome 失败: {e}")
    
    def get_current_page_content(self) -> Dict[str, Any]:
        """
        获取当前活动标签页的内容（不导航）
        
        返回:
            {"title": ..., "url": ..., "content": ...} 或 {"error": ...}
        """
        remote_debugging_url = f"http://{self.vm_ip}:{self.chromium_port}"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(remote_debugging_url)
                
                if not browser.contexts or not browser.contexts[0].pages:
                    return {"error": "No pages found"}
                
                page = browser.contexts[0].pages[0]
                
                result = {
                    "title": page.title(),
                    "url": page.url,
                    "content": page.content()
                }
                
                browser.close()
                return result
                
        except Exception as e:
            return {"error": f"Error: {str(e)}"}
    
    def get_all_tabs_content(self) -> List[Dict[str, Any]]:
        """
        获取所有标签页的内容
        
        返回:
            [{"title": ..., "url": ..., "content": ...}, ...]
        """
        remote_debugging_url = f"http://{self.vm_ip}:{self.chromium_port}"
        results = []
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(remote_debugging_url)
                
                for context in browser.contexts:
                    for page in context.pages:
                        try:
                            results.append({
                                "title": page.title(),
                                "url": page.url,
                                "content": page.content()
                            })
                        except Exception as e:
                            results.append({
                                "url": page.url,
                                "error": str(e)
                            })
                
                browser.close()
                
        except Exception as e:
            results.append({"error": f"Connection error: {str(e)}"})
        
        return results


# ============================================================================
# 购物车评价器
# ============================================================================

class VMCartEvaluator:
    """
    虚拟机购物车评价器
    
    功能:
    - 从虚拟机获取购物车页面内容
    - 检测页面中是否包含期望的商品
    - 计算评分
    """
    
    def __init__(self):
        self.checkpoints: List[Checkpoint] = []
        self.results: List[CartResult] = []
    
    def _get_domain(self, url: str) -> str:
        """提取域名"""
        return urlparse(url).netloc
    
    def _slug(self, url: str) -> str:
        """提取产品 slug"""
        return urlparse(url).path.rstrip("/").split("/")[-1]
    
    def _detect_cart_products(self, html: str, page_url: str) -> Tuple[List[Dict], set]:
        """
        从页面 HTML 中检测购物车商品
        
        参数:
            html: 页面 HTML 内容
            page_url: 页面 URL
            
        返回:
            (所有检测到的商品列表, 检测到的 slug 集合)
        """
        soup = BeautifulSoup(html, "html.parser")
        products = []
        detected_slugs = set()
        
        # 方法1: 从购物车表格中提取
        for row in soup.select("tr.wc-block-cart-items__row, tr.cart_item"):
            a = row.find("a", href=True)
            if a and "/product/" in a.get("href", ""):
                href = a.get("href", "")
                slug = self._slug(href)
                
                # 获取数量
                qty_input = row.find("input", {"type": "number"})
                qty = qty_input.get("value", "1") if qty_input else "1"
                
                products.append({
                    "slug": slug,
                    "name": a.get_text().strip(),
                    "url": href,
                    "quantity": qty,
                    "method": "cart_table"
                })
                detected_slugs.add(slug)
        
        # 方法2: 从 td.product-name 中提取
        for td in soup.select("td.product-name"):
            a = td.find("a", href=True)
            if a and "/product/" in a.get("href", ""):
                href = a.get("href", "")
                slug = self._slug(href)
                
                if slug not in detected_slugs:
                    products.append({
                        "slug": slug,
                        "name": a.get_text().strip(),
                        "url": href,
                        "method": "product_name_cell"
                    })
                    detected_slugs.add(slug)
        
        # 方法3: 检测"已添加到购物车"横幅
        banner = (
            soup.select_one(".woocommerce-message") or
            soup.select_one(".wc-block-components-notice-banner__content")
        )
        if banner and "/product/" in page_url:
            slug = self._slug(page_url)
            if slug not in detected_slugs:
                products.append({
                    "slug": slug,
                    "name": banner.get_text().strip()[:50],
                    "url": page_url,
                    "method": "added_banner"
                })
                detected_slugs.add(slug)
        
        # 方法4: 检测产品概览页的已添加标记
        for li in soup.select("li.product"):
            link = li.find("a", href=True)
            added_btn = li.find("a", class_="added_to_cart")
            if link and added_btn and "/product/" in link.get("href", ""):
                href = link.get("href", "")
                slug = self._slug(href)
                
                if slug not in detected_slugs:
                    products.append({
                        "slug": slug,
                        "name": link.get_text().strip(),
                        "url": href,
                        "method": "overview_added"
                    })
                    detected_slugs.add(slug)
        
        return products, detected_slugs
    
    def create_checkpoints(self, expected_products: List[str]) -> List[Checkpoint]:
        """
        创建检查点
        
        参数:
            expected_products: 期望的产品 URL 列表
            
        返回:
            检查点列表
        """
        self.checkpoints = []
        weight = 1.0 / len(expected_products) if expected_products else 0
        
        for i, url in enumerate(expected_products, 1):
            self.checkpoints.append(Checkpoint(
                id=f"cart_{i}",
                value=url,
                type="cart",
                weight=weight
            ))
        
        return self.checkpoints
    
    def eval_page(self, html: str, page_url: str, vm_name: str) -> CartResult:
        """
        评估单个页面
        
        参数:
            html: 页面 HTML 内容
            page_url: 页面 URL
            vm_name: 虚拟机名称
            
        返回:
            检测结果
        """
        result = CartResult(
            vm_name=vm_name,
            cart_url=page_url,
            actual_url=page_url
        )
        
        current_domain = self._get_domain(page_url)
        
        # 检测购物车商品
        products, detected_slugs = self._detect_cart_products(html, page_url)
        result.detected_products = products
        
        # 与检查点匹配
        for cp in self.checkpoints:
            cp_slug = self._slug(cp.value)
            cp_domain = self._get_domain(cp.value)
            
            # 检查 slug 和域名是否都匹配
            if cp_slug in detected_slugs and cp_domain == current_domain:
                if not cp.flag:
                    cp.flag = True
                    result.score += cp.weight
                    result.matched_checkpoints.append(cp.id)
        
        return result
    
    def eval_vm(self, config: VMConfig) -> List[CartResult]:
        """
        评估单个虚拟机
        
        参数:
            config: 虚拟机配置
            
        返回:
            该虚拟机的所有检测结果
        """
        fetcher = VMPageFetcher(
            config.vm_ip,
            config.chromium_port,
            config.server_port
        )
        
        results = []
        
        for cart_url in config.cart_urls:
            print(f"  获取 {cart_url}...")
            
            page_data = fetcher.get_page_content(cart_url)
            
            if "error" in page_data:
                result = CartResult(
                    vm_name=config.name,
                    cart_url=cart_url,
                    error=page_data["error"]
                )
            else:
                result = self.eval_page(
                    page_data["content"],
                    page_data["url"],
                    config.name
                )
                result.page_title = page_data.get("title", "")
            
            results.append(result)
            self.results.append(result)
        
        return results
    
    def eval_multiple_vms(self, configs: List[VMConfig]) -> Dict[str, Any]:
        """
        评估多个虚拟机
        
        参数:
            configs: 虚拟机配置列表
            
        返回:
            汇总结果
        """
        self.results = []
        
        for config in configs:
            print(f"\n{'='*60}")
            print(f"评估虚拟机: {config.name} ({config.vm_ip})")
            print(f"{'='*60}")
            self.eval_vm(config)
        
        # 计算汇总
        total_score = sum(r.score for r in self.results if r.error is None)
        matched_count = sum(len(r.matched_checkpoints) for r in self.results)
        
        return {
            "vm_results": [asdict(r) for r in self.results],
            "checkpoints": [asdict(cp) for cp in self.checkpoints],
            "total_score": total_score,
            "matched_count": matched_count,
            "total_expected": len(self.checkpoints),
            "success_rate": matched_count / len(self.checkpoints) if self.checkpoints else 0
        }


# ============================================================================
# 输出函数
# ============================================================================

def print_results(results: Dict[str, Any]):
    """打印评估结果"""
    print("\n" + "=" * 70)
    print("虚拟机购物车评估结果")
    print("=" * 70)
    
    for vm_result in results["vm_results"]:
        print(f"\n【{vm_result['vm_name']}】")
        print(f"  购物车 URL: {vm_result['cart_url']}")
        
        if vm_result.get("error"):
            print(f"  ❌ 错误: {vm_result['error']}")
            continue
        
        if vm_result.get("page_title"):
            print(f"  页面标题: {vm_result['page_title']}")
        
        if vm_result["detected_products"]:
            print(f"  检测到的商品 ({len(vm_result['detected_products'])} 个):")
            for product in vm_result["detected_products"]:
                print(f"    - {product['slug']}")
                print(f"      名称: {product.get('name', 'N/A')}")
                print(f"      方法: {product.get('method', 'N/A')}")
        else:
            print(f"  检测到的商品: (无)")
        
        if vm_result["matched_checkpoints"]:
            print(f"  ✓ 匹配的检查点: {', '.join(vm_result['matched_checkpoints'])}")
        
        print(f"  本机得分: {vm_result['score']:.4f}")
    
    print("\n" + "-" * 70)
    print("汇总")
    print("-" * 70)
    print(f"  期望产品数: {results['total_expected']}")
    print(f"  成功匹配数: {results['matched_count']}")
    print(f"  总得分: {results['total_score']:.4f}")
    print(f"  成功率: {results['success_rate']*100:.1f}%")
    
    print("\n检查点状态:")
    for cp in results["checkpoints"]:
        status = "✓" if cp["flag"] else "✗"
        slug = cp["value"].split("/")[-1]
        print(f"  {status} {cp['id']}: {slug}")


# ============================================================================
# 主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="虚拟机购物车评价器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:

  1. 单虚拟机测试:
     python vm_cart_evaluator.py \\
         --vm-ip 10.1.110.114 \\
         --cart-url http://localhost:8081/cart \\
         --expected http://localhost:8081/product/amd-ryzen-9-5900x

  2. 多虚拟机测试:
     python vm_cart_evaluator.py --config vm_config.json

  3. 获取当前页面内容（测试连接）:
     python vm_cart_evaluator.py --vm-ip 10.1.110.114 --test-connection
        """
    )
    
    parser.add_argument("--vm-ip", help="虚拟机 IP 地址")
    parser.add_argument("--chromium-port", type=int, default=9222,
                        help="Chrome 远程调试端口 (默认: 9222)")
    parser.add_argument("--server-port", type=int, default=5000,
                        help="Python server 端口 (默认: 5000)")
    parser.add_argument("--cart-url", action="append", dest="cart_urls",
                        help="购物车 URL (可多次指定)")
    parser.add_argument("--expected", action="append", dest="expected_products",
                        help="期望的产品 URL (可多次指定)")
    parser.add_argument("--config", help="配置文件路径 (JSON)")
    parser.add_argument("--test-connection", action="store_true",
                        help="测试连接并显示当前页面信息")
    parser.add_argument("--output", "-o", help="输出结果到文件 (JSON)")
    
    args = parser.parse_args()
    
    if args.test_connection and args.vm_ip:
        # 测试连接
        print(f"测试连接到 {args.vm_ip}:{args.chromium_port}...")
        fetcher = VMPageFetcher(args.vm_ip, args.chromium_port, args.server_port)
        result = fetcher.get_current_page_content()
        
        if "error" in result:
            print(f"❌ 连接失败: {result['error']}")
        else:
            print(f"✓ 连接成功!")
            print(f"  当前页面: {result['url']}")
            print(f"  标题: {result['title']}")
            print(f"  HTML 长度: {len(result['content'])} 字符")
        return
    
    evaluator = VMCartEvaluator()
    
    if args.config:
        # 从配置文件加载
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        evaluator.create_checkpoints(config["expected_products"])
        
        vm_configs = []
        for vm in config["vms"]:
            vm_configs.append(VMConfig(
                name=vm["name"],
                vm_ip=vm["vm_ip"],
                chromium_port=vm.get("chromium_port", 9222),
                server_port=vm.get("server_port", 5000),
                cart_urls=vm["cart_urls"]
            ))
        
        results = evaluator.eval_multiple_vms(vm_configs)
        
    elif args.vm_ip and args.cart_urls:
        # 命令行参数
        expected_products = args.expected_products or []
        if not expected_products:
            print("警告: 未指定期望的产品，将只检测购物车内容")
        
        evaluator.create_checkpoints(expected_products)
        
        vm_config = VMConfig(
            name="VM",
            vm_ip=args.vm_ip,
            chromium_port=args.chromium_port,
            server_port=args.server_port,
            cart_urls=args.cart_urls
        )
        
        results = evaluator.eval_multiple_vms([vm_config])
        
    else:
        parser.print_help()
        return
    
    # 打印结果
    print_results(results)
    
    # 保存结果
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
