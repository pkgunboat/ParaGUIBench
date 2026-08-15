"""WebMall 服务地址、订单证据与 Attempt 生命周期接口。"""

from paraguibench.integrations.webmall.distributed_lease import (
    HTTPJSONLeaseTransport,
    LEASE_PROTOCOL_ID,
    WebMallDistributedLeaseClient,
    WebMallDistributedLeaseError,
    WebMallLeaseGrant,
    WebMallLeaseTransport,
    build_webmall_distributed_lease_client,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallBrowserRuntime,
    WebMallEnvironmentManifest,
    WebMallEnvironmentManifestError,
    WebMallLeaseContract,
    WebMallOrderReaderContract,
    WebMallResetContract,
    WebMallServiceImages,
    WebMallSoftwareVersions,
    WebMallStoreBinding,
    bind_webmall_origins,
    load_webmall_environment_manifest,
    load_webmall_environment_manifest_with_sha256,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_STORE_UNIVERSE_ID,
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
    ObservedCheckoutProduct,
    ObservedCheckoutProfile,
)
from paraguibench.integrations.webmall.order_evidence import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallGlobalAttemptLease,
    WebMallOrderEvidenceContractError,
    WebMallOrderEvidenceSession,
    WebMallOrderEvidenceSource,
)
from paraguibench.integrations.webmall.registry import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)
from paraguibench.integrations.webmall.report import (
    INVALID_REPORTED_LOGICAL_URL,
    extract_reported_logical_product_urls,
)
from paraguibench.integrations.webmall.wp_order_parser import (
    MAX_WP_CLI_ORDER_PAYLOAD_BYTES,
    parse_wp_cli_order_payload,
)
from paraguibench.integrations.webmall.wpcli_order_source import (
    BoundedProcessRequest,
    BoundedProcessResult,
    BoundedProcessRunner,
    SubprocessBoundedProcessRunner,
    WebMallWPCLIOrderEvidenceSource,
    WebMallWPCLIOrderSourceError,
)

__all__ = [
    "LEASE_PROTOCOL_ID",
    "MAX_WP_CLI_ORDER_PAYLOAD_BYTES",
    "WEBMALL_LOGICAL_STORE_IDS",
    "WEBMALL_STORE_UNIVERSE_ID",
    "BoundedProcessRequest",
    "BoundedProcessResult",
    "BoundedProcessRunner",
    "CheckoutObservationBatch",
    "HTTPJSONLeaseTransport",
    "ObservedCheckoutOrder",
    "ObservedCheckoutProduct",
    "ObservedCheckoutProfile",
    "SubprocessBoundedProcessRunner",
    "WebMallBrowserRuntime",
    "WebMallDistributedLeaseClient",
    "WebMallDistributedLeaseError",
    "WebMallEnvironmentManifest",
    "WebMallEnvironmentManifestError",
    "WebMallLeaseContract",
    "WebMallLeaseGrant",
    "WebMallLeaseTransport",
    "WebMallOrderReaderContract",
    "WebMallResetContract",
    "WebMallServiceImages",
    "WebMallSoftwareVersions",
    "WebMallStoreBinding",
    "WebMallGlobalAttemptLease",
    "WebMallOrderEvidenceContractError",
    "WebMallOrderEvidenceSession",
    "WebMallOrderEvidenceSource",
    "WebMallURLRegistry",
    "WebMallURLRegistryError",
    "WebMallWPCLIOrderEvidenceSource",
    "WebMallWPCLIOrderSourceError",
    "INVALID_REPORTED_LOGICAL_URL",
    "extract_reported_logical_product_urls",
    "bind_webmall_origins",
    "build_webmall_distributed_lease_client",
    "load_webmall_environment_manifest",
    "load_webmall_environment_manifest_with_sha256",
    "parse_wp_cli_order_payload",
]
