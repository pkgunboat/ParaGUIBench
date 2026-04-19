#!/usr/bin/env python3
"""
基于 AT 的 Checkout 评价器

功能：
- 从虚拟机的 AT 中提取 checkout 订单确认页面信息
- 验证商品、账单地址、邮箱是否与预期一致

使用方法:
    python checkout_evaluator_from_at.py --vm-ip 10.1.110.114 --server-port 5000
    
    # 指定期望的商品和用户信息
    python checkout_evaluator_from_at.py --vm-ip 10.1.110.114 --server-port 5000 \
        --product-slug "trust-tk-350-wireless-membrane-keyboard" \
        --name "Jessica Morgan" --email "jessica.morgan@yahoo.com" \
        --street "Maple Avenue" --house-number "742" --zip "60614" \
        --city "Chicago" --state "IL" --country "USA"
"""

import requests
import json
import argparse
import re
import html
from typing import Dict, Optional, List
from urllib.parse import urlparse
from dataclasses import dataclass, field


@dataclass
class ExpectedCheckout:
    """预期的 checkout 信息"""
    product_slug: str
    shop_port: int
    user_details: Dict[str, str]


@dataclass 
class CheckoutResult:
    """Checkout 检测结果"""
    is_checkout_page: bool = False
    page_url: str = ""
    order_number: str = ""
    product_slug: str = ""
    product_name: str = ""
    
    # 账单信息（从 AT 提取的原始数据）
    billing_info: List[str] = field(default_factory=list)
    billing_email: str = ""
    
    # 验证结果
    checks: Dict[str, bool] = field(default_factory=dict)

    # History 回溯相关
    recovery_used: bool = False      # 是否使用了 Chrome History 回溯
    recovery_url: str = ""           # 回溯使用的 URL

    error: str = None


def get_at(vm_ip: str, port: int) -> Optional[str]:
    """
    获取 Accessibility Tree
    
    参数:
        vm_ip: 虚拟机 IP
        port: 服务端口
        
    返回:
        AT 字符串，失败返回 None
    """
    try:
        resp = requests.get(f"http://{vm_ip}:{port}/accessibility", timeout=30)
        if resp.status_code == 200:
            data = resp.json() if 'application/json' in resp.headers.get('content-type', '') else {}
            return data.get("AT", resp.text)
    except Exception as e:
        print(f"获取 AT 失败: {e}")
    return None


def name_to_slug(name: str) -> str:
    """
    将商品名称转换为 slug 格式
    
    参数:
        name: 商品名称
        
    返回:
        slug 格式字符串
    """
    # 循环解码 HTML 实体，处理多重编码（如 &amp;amp; → &amp; → &）
    prev = None
    while prev != name:
        prev = name
        name = html.unescape(name)
    slug = name.lower()
    slug = re.sub(r'[^\w\s-]', ' ', slug)
    slug = re.sub(r'\s+', ' ', slug).strip()
    slug = slug.replace(' ', '-')
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _execute_on_vm(vm_ip: str, port: int, command_list: List[str], timeout: int = 30) -> Optional[str]:
    """
    通过 VM Python Server 的 /execute 接口执行命令

    参数:
        vm_ip: 虚拟机 IP
        port: 服务端口
        command_list: 命令列表（如 ["python3", "-c", "print('hello')"]）
        timeout: 请求超时时间（秒）

    返回:
        命令的 stdout 输出，失败返回 None
    """
    try:
        url = f"http://{vm_ip}:{port}/execute"
        payload = json.dumps({"command": command_list, "shell": False})
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            data=payload,
            timeout=timeout
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("output", "")
        else:
            print(f"_execute_on_vm 返回非 200 状态码: {resp.status_code}")
            return None
    except Exception as e:
        print(f"_execute_on_vm 执行失败: {e}")
        return None


def get_order_received_url_from_history(vm_ip: str, port: int) -> Optional[str]:
    """
    从 Chrome History SQLite 中查找最近的 order-received URL

    原理：复制 Chrome History 到 /tmp（避免数据库锁冲突），查询 urls 表中
    包含 'order-received' 的 URL，按最后访问时间倒序取第一条。

    参数:
        vm_ip: 虚拟机 IP
        port: 服务端口

    返回:
        最近的 order-received URL，未找到返回 None
    """
    # 在 VM 上执行的 Python 脚本
    script = (
        "import shutil, sqlite3, os\n"
        "src = '/home/user/.config/google-chrome/Default/History'\n"
        "dst = '/tmp/chrome_history_copy'\n"
        "if not os.path.exists(src):\n"
        "    print('NO_HISTORY_FILE')\n"
        "else:\n"
        "    shutil.copy2(src, dst)\n"
        "    conn = sqlite3.connect(dst)\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\n"
        "        \"SELECT url FROM urls WHERE url LIKE '%order-received%' \"\n"
        "        \"ORDER BY last_visit_time DESC LIMIT 1\"\n"
        "    )\n"
        "    row = cur.fetchone()\n"
        "    conn.close()\n"
        "    os.remove(dst)\n"
        "    if row:\n"
        "        print(row[0])\n"
        "    else:\n"
        "        print('NO_ORDER_URL')\n"
    )

    output = _execute_on_vm(vm_ip, port, ["python3", "-c", script], timeout=15)
    if output is None:
        return None

    output = output.strip()
    if output in ("NO_HISTORY_FILE", "NO_ORDER_URL", ""):
        print(f"Chrome History 查询结果: {output}")
        return None

    return output


def navigate_to_url(vm_ip: str, port: int, url: str, wait_seconds: int = 5) -> bool:
    """
    在 VM 上用 xdg-open 打开指定 URL，并等待页面加载

    参数:
        vm_ip: 虚拟机 IP
        port: 服务端口
        url: 要导航到的 URL
        wait_seconds: 导航后等待的秒数

    返回:
        是否成功发送导航命令
    """
    # 使用 bash -c 设置 DISPLAY 并执行 xdg-open
    nav_script = (
        f"import subprocess, os, time\n"
        f"env = os.environ.copy()\n"
        f"env['DISPLAY'] = ':0'\n"
        f"try:\n"
        f"    subprocess.Popen(['xdg-open', '{url}'], env=env,\n"
        f"        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"    time.sleep({wait_seconds})\n"
        f"    print('NAV_OK')\n"
        f"except Exception as e:\n"
        f"    print(f'NAV_FAIL: {{e}}')\n"
    )

    output = _execute_on_vm(vm_ip, port, ["python3", "-c", nav_script], timeout=wait_seconds + 15)
    if output and "NAV_OK" in output:
        return True
    print(f"navigate_to_url 失败: {output}")
    return False


def extract_checkout_info_with_recovery(vm_ip: str, port: int) -> CheckoutResult:
    """
    带 Chrome History 回溯的 checkout 信息提取主入口

    流程：
    1. 获取当前 AT → 尝试正常提取 checkout 信息
    2. 如果当前页面是 order-received，直接返回结果（正常路径）
    3. 否则从 Chrome History 中查找 order-received URL
    4. 如果找到，导航回该页面，重新获取 AT 并提取
    5. 返回结果（附带 recovery 标记）

    参数:
        vm_ip: 虚拟机 IP
        port: 服务端口

    返回:
        CheckoutResult 检测结果
    """
    # 第一步：获取当前 AT
    at = get_at(vm_ip, port)
    if not at:
        result = CheckoutResult()
        result.error = "无法获取 AT"
        return result

    # 第二步：尝试正常提取
    result = extract_checkout_info(at)
    if result.is_checkout_page:
        # 当前页面就是 order-received，直接返回
        return result

    # 第三步：当前页面不是 order-received，尝试从 Chrome History 回溯
    print("当前页面不是 order-received，尝试从 Chrome History 回溯...")
    history_url = get_order_received_url_from_history(vm_ip, port)
    if not history_url:
        print("Chrome History 中未找到 order-received URL，评估失败")
        return result  # 返回原始的"非 checkout 页面"结果

    print(f"从 Chrome History 找到 order-received URL: {history_url}")

    # 第四步：导航回 order-received 页面
    nav_ok = navigate_to_url(vm_ip, port, history_url)
    if not nav_ok:
        result.error = f"导航回 order-received 页面失败: {history_url}"
        return result

    # 第五步：重新获取 AT 并提取
    at_retry = get_at(vm_ip, port)
    if not at_retry:
        result.error = "回溯导航后无法获取 AT"
        return result

    result = extract_checkout_info(at_retry)
    result.recovery_used = True
    result.recovery_url = history_url

    if result.is_checkout_page:
        print("Chrome History 回溯成功，已恢复到 order-received 页面")
    else:
        print("Chrome History 回溯后仍未检测到 order-received 页面")

    return result


def extract_checkout_info(at: str) -> CheckoutResult:
    """
    从 AT 中提取 checkout 订单确认页面信息

    参数:
        at: Accessibility Tree 字符串

    返回:
        CheckoutResult 检测结果
    """
    result = CheckoutResult()

    # 提取当前 URL
    url_match = re.search(r'name="Address and search bar"[^>]*>([^<]+)<', at)
    if url_match:
        result.page_url = url_match.group(1).strip()
        if not result.page_url.startswith("http"):
            result.page_url = f"http://{result.page_url}"

    # 检查是否是 order-received 页面
    if 'order-received' in result.page_url.lower() or 'order-received' in at.lower():
        result.is_checkout_page = True
    else:
        result.error = "当前页面不是 checkout 订单确认页面"
        return result

    # 提取订单号
    order_match = re.search(r'order-received/(\d+)', result.page_url)
    if order_match:
        result.order_number = order_match.group(1)

    # 提取所有 static 元素
    static_names = re.findall(r'<static name="([^"]+)"', at)

    # 提取商品信息
    for name in static_names:
        # 跳过常见的非商品文本，查找较长的产品描述
        if len(name) > 30 and not any(k in name.lower() for k in
            ['thank you', 'order', 'billing', 'total', 'subtotal', 'address',
             'payment', 'received', 'details', 'number', 'date', 'email',
             'contact', 'theme', 'products', 'shop', 'cart',
             'pay faster', 'save your card', 'security code', 'next time']):
            if result.product_name == "" or len(name) > len(result.product_name):
                result.product_name = name
                result.product_slug = name_to_slug(name)

    # 提取账单信息（从 "Billing address" 之后的元素）
    billing_start = False
    for name in static_names:
        if 'Billing address' in name:
            billing_start = True
            continue
        if billing_start:
            # 遇到非地址内容时停止
            if any(k in name.lower() for k in ['product', 'contact', 'theme', 'shop', 'cart', 'categories']):
                break
            # 跳过空白和换行符
            if name and name != '&#10;' and len(name) > 1:
                result.billing_info.append(name)
                # 检查是否是邮箱
                if '@' in name and '.' in name:
                    result.billing_email = name
                # 收集足够信息后停止
                if len(result.billing_info) >= 7:
                    break

    return result


def verify_checkout(result: CheckoutResult, expected: ExpectedCheckout) -> CheckoutResult:
    """
    验证 checkout 结果是否符合预期
    
    参数:
        result: 检测结果
        expected: 预期信息
        
    返回:
        更新后的检测结果
    """
    # 将账单信息合并为一个字符串进行检查
    billing_text = ' '.join(result.billing_info).lower()

    # 从页面 URL 中提取实际商店端口，与期望端口对比
    actual_port = 0
    if result.page_url:
        parsed = urlparse(result.page_url)
        actual_port = parsed.port or 0

    # 验证各项
    result.checks = {
        '商店': (expected.shop_port == 0 or actual_port == expected.shop_port),
        '商品': (expected.product_slug.replace('-', ' ') in result.product_name.lower() or
                expected.product_slug in result.product_slug),
        '姓名': expected.user_details.get('name', '').lower() in billing_text,
        '街道': expected.user_details.get('street', '').lower() in billing_text,
        '门牌号': expected.user_details.get('house_number', '') in billing_text,
        '邮编': expected.user_details.get('zip', '') in billing_text,
        '国家': ('united states' in billing_text or
                expected.user_details.get('country', '').lower() in billing_text),
        '邮箱': expected.user_details.get('email', '').lower() in billing_text,
    }
    
    return result


def print_result(result: CheckoutResult, expected: ExpectedCheckout):
    """
    打印评价结果
    
    参数:
        result: 检测结果
        expected: 预期信息
    """
    print("\n" + "=" * 70)
    print("Checkout 评价结果")
    print("=" * 70)
    
    if result.error:
        print(f"\n❌ 错误: {result.error}")
        return
    
    print(f"\n页面 URL: {result.page_url}")
    print(f"订单号: {result.order_number}")
    
    print("\n" + "-" * 70)
    print("从 AT 中提取的账单信息:")
    print("-" * 70)
    for info in result.billing_info:
        print(f"  - {info}")
    
    print("\n" + "-" * 70)
    print("商品信息:")
    print("-" * 70)
    print(f"  期望: {expected.product_slug}")
    print(f"  检测: {result.product_name[:60]}...")
    
    print("\n" + "-" * 70)
    print("验证结果:")
    print("-" * 70)
    
    for item, ok in result.checks.items():
        status = '✓' if ok else '✗'
        print(f"  {status} {item}")
    
    # 总分
    score = sum(result.checks.values()) / len(result.checks) if result.checks else 0
    all_ok = all(result.checks.values()) if result.checks else False
    
    print("\n" + "=" * 70)
    print(f"得分: {score:.1%}")
    print(f"结果: {'✓ CHECKOUT 成功' if all_ok else '✗ CHECKOUT 未完成'}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Checkout 评价器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认任务 (Webmall_Checkout_Task1)
  python checkout_evaluator_from_at.py --vm-ip 10.1.110.114 --server-port 5000
  
  # 自定义商品和用户信息
  python checkout_evaluator_from_at.py --vm-ip 10.1.110.114 --server-port 5000 \\
      --product-slug "my-product-slug" \\
      --name "John Doe" --email "john@example.com"
        """
    )
    
    parser.add_argument("--vm-ip", default="10.1.110.114", help="虚拟机 IP")
    parser.add_argument("--server-port", type=int, default=5000, help="服务端口")
    
    # 商品信息
    parser.add_argument("--product-slug", 
        default="trust-tk-350-wireless-membrane-keyboard-spill-proof-silent-keys-media-keys-black",
        help="期望的商品 slug")
    parser.add_argument("--shop-port", type=int, default=9083, help="商店端口")
    
    # 用户信息
    parser.add_argument("--name", default="Jessica Morgan", help="姓名")
    parser.add_argument("--email", default="jessica.morgan@yahoo.com", help="邮箱")
    parser.add_argument("--street", default="Maple Avenue", help="街道")
    parser.add_argument("--house-number", default="742", help="门牌号")
    parser.add_argument("--zip", default="60614", help="邮编")
    parser.add_argument("--city", default="Chicago", help="城市")
    parser.add_argument("--state", default="IL", help="州")
    parser.add_argument("--country", default="USA", help="国家")
    
    args = parser.parse_args()
    
    # 构建期望信息
    expected = ExpectedCheckout(
        product_slug=args.product_slug,
        shop_port=args.shop_port,
        user_details={
            "name": args.name,
            "street": args.street,
            "house_number": args.house_number,
            "zip": args.zip,
            "city": args.city,
            "state": args.state,
            "country": args.country,
            "email": args.email
        }
    )
    
    print("=" * 70)
    print("Checkout 评价器")
    print("=" * 70)
    print(f"\n商品 slug: {expected.product_slug}")
    print(f"\n用户信息:")
    for k, v in expected.user_details.items():
        print(f"  {k}: {v}")
    print("\n" + "=" * 70)
    
    print(f"\n正在获取 {args.vm_ip}:{args.server_port} 的页面信息...")
    
    at = get_at(args.vm_ip, args.server_port)
    if not at:
        print("❌ 无法获取 AT")
        return 1
    
    result = extract_checkout_info(at)
    result = verify_checkout(result, expected)
    
    print_result(result, expected)
    
    # 返回是否成功
    if result.checks:
        return 0 if all(result.checks.values()) else 1
    return 1


if __name__ == "__main__":
    exit(main())
