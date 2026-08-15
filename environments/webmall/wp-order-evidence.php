<?php
/**
 * ParaGUIBench WebMall 权威订单证据导出器。
 *
 * 本文件由受信任部署层通过 WP-CLI eval-file 调用。它只读取 WooCommerce
 * domain API，不读取浏览器历史，不接受任务提供的命令，也不输出卡号、CVV、
 * 有效期、订单访问 key 或 URL。
 */

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(E_ALL);

const PARAGUIBENCH_WEBMALL_READER_SCHEMA_VERSION = 2;
const PARAGUIBENCH_WEBMALL_MAX_DETAIL_IDS = 128;

/**
 * 功能：把一个 WooCommerce 订单行转换为固定字段闭集。
 *
 * 输入参数：
 *   $item：WooCommerce WC_Order_Item_Product 实例。
 * 输出返回值：
 *   包含 product_id、variation_id、quantity 与可信 canonical_slug 的数组。
 * 异常：
 *   RuntimeException：商品身份、slug 或数量无法完整确定。
 */
function paraguibench_webmall_item_to_array($item): array
{
    if (!($item instanceof WC_Order_Item_Product)) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $product_id = (int) $item->get_product_id();
    $variation_id = (int) $item->get_variation_id();
    $quantity = (int) $item->get_quantity();
    if ($product_id <= 0 || $variation_id < 0 || $quantity <= 0) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $canonical_slug = get_post_field('post_name', $product_id);
    if (!is_string($canonical_slug) || $canonical_slug === '') {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    return array(
        'product_id' => $product_id,
        'variation_id' => $variation_id,
        'quantity' => $quantity,
        'canonical_slug' => $canonical_slug,
    );
}

/**
 * 功能：把一个 WooCommerce 订单转换为不含支付秘密的固定 JSON 对象。
 *
 * 输入参数：
 *   $order：WooCommerce WC_Order 实例。
 * 输出返回值：
 *   包含订单状态、支付方法 ID、完整 billing 字段与全部商品行的数组。
 * 异常：
 *   RuntimeException：订单类型、身份或商品闭包无法完整读取。
 */
function paraguibench_webmall_order_to_array($order): array
{
    if (!($order instanceof WC_Order)) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $order_id = (int) $order->get_id();
    if ($order_id <= 0) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $items = array();
    foreach ($order->get_items('line_item') as $item) {
        $items[] = paraguibench_webmall_item_to_array($item);
    }
    if (count($items) === 0) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    return array(
        'order_id' => $order_id,
        'status' => (string) $order->get_status(),
        'payment_method' => (string) $order->get_payment_method(),
        'billing' => array(
            'first_name' => (string) $order->get_billing_first_name(),
            'last_name' => (string) $order->get_billing_last_name(),
            'email' => (string) $order->get_billing_email(),
            'address_1' => (string) $order->get_billing_address_1(),
            'postcode' => (string) $order->get_billing_postcode(),
            'city' => (string) $order->get_billing_city(),
            'state' => (string) $order->get_billing_state(),
            'country' => (string) $order->get_billing_country(),
        ),
        'items' => $items,
    );
}

/**
 * 功能：把一个候选值验证为 PHP 可安全表示的正整数订单 ID。
 *
 * 输入参数：
 *   $value：WooCommerce API 或受信 Python source 提供的候选值。
 * 输出返回值：
 *   规范的正整数订单 ID。
 * 异常：
 *   RuntimeException：类型、编码、范围或规范形式无效。
 */
function paraguibench_webmall_parse_order_id($value): int
{
    if (is_int($value)) {
        if ($value <= 0) {
            throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
        }
        return $value;
    }
    if (
        !is_string($value)
        || preg_match('/^[1-9][0-9]{0,18}$/D', $value) !== 1
    ) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $parsed = (int) $value;
    if ($parsed <= 0 || (string) $parsed !== $value) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    return $parsed;
}

/**
 * 功能：将字段闭合的 reader 文档编码并写入标准输出。
 *
 * 输入参数：
 *   $document：仅由固定 reader 函数构造的 JSON 文档数组。
 * 输出返回值：
 *   无；成功时向 STDOUT 写入单个 UTF-8 JSON 文档。
 * 异常：
 *   RuntimeException：JSON 编码失败。
 */
function paraguibench_webmall_write_document(array $document): void
{
    $encoded = wp_json_encode(
        $document,
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
    );
    if (!is_string($encoded)) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    fwrite(STDOUT, $encoded);
}

/**
 * 功能：枚举全部 WooCommerce 订单状态的正整数 ID，不读取任何历史详情。
 *
 * 输入参数：
 *   无；WooCommerce 与 WordPress 已由 WP-CLI 启动。
 * 输出返回值：
 *   无；输出 schema_version=2、mode=identities 的完整 ID 闭集。
 * 异常：
 *   RuntimeException：API 不可用、ID 无效或重复。
 */
function paraguibench_webmall_emit_order_identities(): void
{
    if (!function_exists('wc_get_orders')) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $raw_order_ids = wc_get_orders(array(
        'limit' => -1,
        'orderby' => 'ID',
        'order' => 'ASC',
        'return' => 'ids',
        'status' => array_keys(wc_get_order_statuses()),
    ));
    if (!is_array($raw_order_ids)) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $order_ids = array();
    $seen = array();
    foreach ($raw_order_ids as $raw_order_id) {
        $order_id = paraguibench_webmall_parse_order_id($raw_order_id);
        $key = (string) $order_id;
        if (array_key_exists($key, $seen)) {
            throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
        }
        $seen[$key] = true;
        $order_ids[] = $order_id;
    }
    paraguibench_webmall_write_document(array(
        'schema_version' => PARAGUIBENCH_WEBMALL_READER_SCHEMA_VERSION,
        'mode' => 'identities',
        'complete' => true,
        'order_ids' => $order_ids,
    ));
}

/**
 * 功能：仅读取受信 source 明确请求的新订单完整评价详情。
 *
 * 输入参数：
 *   $raw_order_ids：WP-CLI 位置参数中的有界、唯一数字 ID 列表。
 * 输出返回值：
 *   无；输出 schema_version=2、mode=details 的严格订单闭集。
 * 异常：
 *   RuntimeException：ID 请求、订单或任一新订单详情无效。
 */
function paraguibench_webmall_emit_order_details(array $raw_order_ids): void
{
    if (
        count($raw_order_ids) === 0
        || count($raw_order_ids) > PARAGUIBENCH_WEBMALL_MAX_DETAIL_IDS
        || !function_exists('wc_get_order')
    ) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $records = array();
    $seen = array();
    foreach ($raw_order_ids as $raw_order_id) {
        $order_id = paraguibench_webmall_parse_order_id($raw_order_id);
        $key = (string) $order_id;
        if (array_key_exists($key, $seen)) {
            throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
        }
        $seen[$key] = true;
        $order = wc_get_order($order_id);
        if (!($order instanceof WC_Order) || (int) $order->get_id() !== $order_id) {
            throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
        }
        $records[] = paraguibench_webmall_order_to_array($order);
    }
    paraguibench_webmall_write_document(array(
        'schema_version' => PARAGUIBENCH_WEBMALL_READER_SCHEMA_VERSION,
        'mode' => 'details',
        'complete' => true,
        'orders' => $records,
    ));
}

/**
 * 功能：按 WP-CLI 位置参数分派固定 identities/details 读取模式。
 *
 * 输入参数：
 *   $reader_args：WP-CLI eval-file 传入的位置参数。
 * 输出返回值：
 *   无；仅有一个固定模式会写入响应。
 * 异常：
 *   RuntimeException：模式缺失、未知或携带不允许参数。
 */
function paraguibench_webmall_dispatch(array $reader_args): void
{
    if (count($reader_args) === 0) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    $mode = array_shift($reader_args);
    if ($mode === 'identities') {
        if (count($reader_args) !== 0) {
            throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
        }
        paraguibench_webmall_emit_order_identities();
        return;
    }
    if ($mode === 'details') {
        paraguibench_webmall_emit_order_details($reader_args);
        return;
    }
    throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
}

try {
    if (!isset($args) || !is_array($args)) {
        throw new RuntimeException('WEBMALL_ORDER_READER_ERROR');
    }
    paraguibench_webmall_dispatch($args);
} catch (Throwable $error) {
    fwrite(STDERR, "WEBMALL_ORDER_READER_ERROR\n");
    exit(2);
}
