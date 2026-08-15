"""把确定性 benchmark evaluator 适配为 runtime 统一结果。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from paraguibench.evaluation.answers import evaluate_qa_answer
from paraguibench.evaluation.exact_answer import evaluate_exact_answer
from paraguibench.evaluation.osworld import (
    ARTIFACT_STATE_PROTOCOL_ID,
    CHROME_BOOKMARKS_PROTOCOL_ID,
    CHROME_PROFILE_NAME_PROTOCOL_ID,
    GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    OSWORLD_BOOKMARK_TASK_RULES,
    ArtifactStateTaskRule,
    OSWorldBookmarkEvaluation,
    OSWorldArtifactStateEvaluation,
    OSWorldStateEvaluation,
    evaluate_artifact_state_observations,
    evaluate_chrome_bookmark_observations,
    evaluate_chrome_profile_name_observations,
    evaluate_google_shopping_active_tab_observations,
)
from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    OPERATION_TASK_RULES,
    OperationEvaluation,
    OperationEvaluationError,
    WordAbbreviationBaseline,
    WordAbbreviationError,
    WordTextBaseline,
    WordTextFidelityError,
    evaluate_operation_artifacts,
    operation_word_abbreviation_input_contract,
    operation_word_text_input_contract,
    validate_word_abbreviation_baseline_identity,
    validate_word_text_baseline_identity,
)
from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SEARCHWRITE_XLSX_TASK_ID,
    CrossDocumentEvaluation,
    CrossDocumentObservation,
    HideNARowsEvaluation,
    HideNARowsObservation,
    ImageClassificationEvaluation,
    ImageClassificationObservation,
    SearchWriteEvaluation,
    SearchWriteObservation,
    evaluate_cross_document,
    evaluate_image_classification,
    evaluate_hide_na_rows,
    evaluate_searchwrite_xlsx,
)
from paraguibench.evaluation.webmall import (
    CHECKOUT_PROTOCOL_ID,
    FIND_AND_ORDER_PROTOCOL_ID,
    URL_MULTISET_PROTOCOL_ID,
    CheckoutEvaluation,
    FindAndOrderEvaluation,
    WebMallURLSetEvaluation,
    evaluate_webmall_checkout,
    evaluate_webmall_find_and_order,
    evaluate_webmall_url_set,
)
from paraguibench.evaluation.webmall.cart import (
    CART_PROTOCOL_ID,
    CartEvaluation,
    evaluate_webmall_cart,
)
from paraguibench.integrations.osworld.bookmark_contracts import (
    OSWORLD_BOOKMARK_TASK_BINDINGS,
    BookmarkTaskBinding,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OperationArtifactCaptureError,
    OperationArtifactSnapshot,
)
from paraguibench.integrations.webmall import WebMallURLRegistry
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
)
from paraguibench.runtime.attempt_runner import RuntimeEvaluation


class UnsupportedTaskEvaluatorError(ValueError):
    """表示 canonical task 尚无可安全装配的原生 runtime evaluator。"""


_NATIVE_ANSWER_PROTOCOLS = frozenset(
    {
        "paraguibench.answer.exact.v1",
        "paraguibench.answer.numeric.v1",
        "paraguibench.answer.keyed-numeric-set.v1",
        "paraguibench.answer.ordered-structured.v1",
        "paraguibench.answer.implicit-structured.v1",
    }
)
_WEBMALL_CART_TASK_TAGS = {
    **{
        f"Operation-OnlineShopping-AddToCart-{index:03d}": "AddToCart"
        for index in range(1, 8)
    },
    "Operation-OnlineShopping-CheapestProductSearch-007": ("CheapestProductSearch"),
}
_WORD_TEXT_FIDELITY_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
    }
)
_WORD_ABBREVIATION_TASK_ID = "Operation-FileOperate-BatchOperationWord-012"
_PIPELINE_IMPLICIT_TASK_BINDINGS: dict[str, dict[str, str]] = {
    CROSS_DOCUMENT_TASK_ID: {
        "protocol_id": CROSS_DOCUMENT_PROTOCOL_ID,
        "task_uid": "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8",
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    HIDE_NA_ROWS_TASK_ID: {
        "protocol_id": HIDE_NA_ROWS_PROTOCOL_ID,
        "task_uid": "1c73128f-a5ef-4a97-97ce-ef427d6d46b4",
        "task_type": "OSWorld脚本改造",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    IMAGE_CLASSIFICATION_TASK_ID: {
        "protocol_id": IMAGE_CLASSIFICATION_PROTOCOL_ID,
        "task_uid": "e544ee0f-90e6-43a4-9958-6b74e88d94a6",
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
    SEARCHWRITE_XLSX_TASK_ID: {
        "protocol_id": SEARCHWRITE_XLSX_PROTOCOL_ID,
        "task_uid": "65a4848d-b4b2-4173-8308-a0213fdafbd0",
        "task_type": "",
        "task_source": "self",
        "task_tag": "FileOperate",
        "evaluator_path": "",
    },
}


class AnswerTaskEvaluator:
    """把完整 QA answer evaluator 接到 AttemptRunner 的统一适配器。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """执行确定性 QA 评价并只返回脱敏诊断。

        输入参数：
            task：可信 canonical QA task，包含版本化 answer contract。
            final_output：Agent terminal action 返回的完整文本。
            environment：仍存活的任务环境；纯答案 evaluator 不读取。
        输出返回值：
            passed、确定性 score，以及 match_type 和计数型安全诊断。
        """

        del environment
        result = evaluate_qa_answer(task, final_output)
        return RuntimeEvaluation(
            passed=result.passed,
            score=result.score,
            details={
                "match_type": result.match_type,
                **dict(result.details),
            },
        )


class ExactTaskEvaluator:
    """把 exact answer evaluator 接到 AttemptRunner 的最小适配器。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """执行 deterministic exact 评价并省略全部 gold 文本。

        输入参数：
            task：可信 canonical task，包含 answer contract。
            final_output：Agent terminal action 返回的完整文本。
            environment：仍存活的任务环境；纯文本 evaluator 不读取。
        输出返回值：
            passed、0/1 score 以及仅含 match_type 的 RuntimeEvaluation。
        """

        del environment
        result = evaluate_exact_answer(task, final_output)
        return RuntimeEvaluation(
            passed=result.passed,
            score=result.score,
            details={"match_type": result.match_type},
        )


class WebMallCheckoutTaskEvaluator:
    """把 Checkout/FindAndOrder 闭集协议接到 runtime environment seam。"""

    def __init__(self, *, evaluation_protocol: str) -> None:
        """固定本实例唯一允许执行的 WebMall 协议。

        输入参数：
            evaluation_protocol：Checkout 订单闭集或 FindAndOrder 组合协议 ID。
        输出返回值：
            无；构造阶段不读取 task、环境或订单证据。
        异常：
            UnsupportedTaskEvaluatorError：协议不是本 adapter 的两个固定 ID。
        """

        if evaluation_protocol not in {
            CHECKOUT_PROTOCOL_ID,
            FIND_AND_ORDER_PROTOCOL_ID,
        }:
            raise UnsupportedTaskEvaluatorError(
                "WebMall checkout evaluation protocol 不受支持"
            )
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """读取冻结订单证据，并按任务协议产生 allowlist-only 结果。

        输入参数：
            task：可信 canonical WebMall task，必须包含 logical expected URLs。
            final_output：Agent 最终报告；Checkout 忽略，EndToEnd 严格解析。
            environment：仍存活且实现订单终态/报告 logical 化接口的 WebMall
                环境。
        输出返回值：
            二值 score 与仅含协议、reason code、布尔值和计数的安全详情。
        异常：
            TypeError：环境缺少所需接口。
            evaluator/evidence contract error：原样传播，由 AttemptRunner 记录
                ``EvaluationOutcome.ERROR`` 和 ``score=None``。
        """

        observation_reader = getattr(
            environment,
            "checkout_observation",
            None,
        )
        if not callable(observation_reader):
            raise TypeError("WebMall environment 缺少 checkout observation")
        observation = observation_reader()
        expected_urls = task.get("expected_urls")
        expected_checkout_profile = _checkout_profile_from_task(task)

        if self._evaluation_protocol == CHECKOUT_PROTOCOL_ID:
            evaluation = evaluate_webmall_checkout(
                expected_urls,
                expected_checkout_profile,
                observation,
            )
            return RuntimeEvaluation(
                passed=evaluation.passed,
                score=evaluation.score,
                details=_checkout_details(evaluation),
            )

        report_reader = getattr(
            environment,
            "canonicalize_reported_product_urls",
            None,
        )
        if not callable(report_reader):
            raise TypeError("WebMall environment 缺少 report canonicalizer")
        submitted_logical_urls = report_reader(final_output)
        evaluation = evaluate_webmall_find_and_order(
            expected_urls,
            submitted_logical_urls,
            expected_checkout_profile,
            observation,
        )
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_find_and_order_details(evaluation),
        )


class WebMallURLSetTaskEvaluator:
    """把 WebMall string URL 多集合协议接到 runtime seam。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """规范化 Agent 报告并执行忠实的 URL 多集合评价。

        输入参数：
            task：可信 canonical WebMall QA task，包含 logical
                ``expected_urls``。
            final_output：Agent terminal action 返回的完整报告。
            environment：仍存活且只提供报告 logical 化与
                runtime registry seam 的 WebMall URL 环境。
        输出返回值：
            保留 pure evaluator score 的 ``RuntimeEvaluation``；details
            只含协议、计数和 precision/recall/F1。
        异常：
            TypeError：环境缺少所需 seam，或 seam 返回值类型无效。
            WebMallURLRegistryError：gold 不能由固定四店 registry
                解析；由 AttemptRunner 记为 evaluator error。
        """

        report_reader = getattr(
            environment,
            "canonicalize_reported_product_urls",
            None,
        )
        registry_reader = getattr(environment, "webmall_url_registry", None)
        if not callable(report_reader) or not callable(registry_reader):
            raise TypeError("WebMall URL environment 缺少 evaluator seam")
        submitted_logical_urls = report_reader(final_output)
        registry = registry_reader()
        if not isinstance(submitted_logical_urls, tuple):
            raise TypeError("WebMall URL report 必须是 tuple")
        if not isinstance(registry, WebMallURLRegistry):
            raise TypeError("WebMall URL registry 类型无效")
        evaluation = evaluate_webmall_url_set(
            task.get("expected_urls"),
            submitted_logical_urls,
            registry,
        )
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_webmall_url_set_details(evaluation),
        )


class WebMallCartTaskEvaluator:
    """把 8 个 WebMall Cart 闭集任务接到浏览器终态证据。"""

    def __init__(self, *, task_id: str, evaluation_protocol: str) -> None:
        """固定本实例唯一允许执行的已审计 Cart 任务与协议。

        输入参数：
            task_id：必须位于 7 个 AddToCart 与指定 CheapestProductSearch
                的 8-task 闭集。
            evaluation_protocol：必须精确等于 Cart closed-world v1。
        输出返回值：
            无；构造阶段不读取浏览器、Cart、Agent 文本或 gold。
        异常：
            UnsupportedTaskEvaluatorError：任务或协议不在正式闭集。
        """

        if (
            evaluation_protocol != CART_PROTOCOL_ID
            or task_id not in _WEBMALL_CART_TASK_TAGS
        ):
            raise UnsupportedTaskEvaluatorError(
                "WebMall cart evaluation protocol 不受支持"
            )
        self._task_id = task_id
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """只读取冻结 Cart 证据并返回 allowlist-only 计数结果。

        输入参数：
            task：必须仍精确匹配构造时绑定的 canonical Cart task。
            final_output：Agent 最终文本；Cart 状态协议明确忽略该值。
            environment：仍存活且提供 ``cart_observation`` 的 Cart 环境。
        输出返回值：
            二值 score，以及只含协议、原因码和非敏感计数的
            ``RuntimeEvaluation``。
        异常：
            UnsupportedTaskEvaluatorError：task 身份在装配后发生漂移。
            TypeError：环境 seam 或 observation 类型无效。
            WebMallCartEvaluationError：gold 或证据不完整；由
                AttemptRunner 记为 evaluation ERROR/null。
        """

        del final_output
        if task.get("task_id") != self._task_id or not _matches_webmall_cart_task(task):
            raise UnsupportedTaskEvaluatorError("WebMall cart task contract 不匹配")
        observation_reader = getattr(
            environment,
            "cart_observation",
            None,
        )
        if not callable(observation_reader):
            raise TypeError("WebMall environment 缺少 cart observation")
        observation = observation_reader()
        if not isinstance(observation, CartObservationBatch):
            raise TypeError("WebMall cart observation 类型无效")
        evaluation = evaluate_webmall_cart(
            task.get("expected_urls"),
            observation,
        )
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_webmall_cart_details(evaluation),
        )


class OSWorldStateTaskEvaluator:
    """把 Chrome profile/active-tab 状态协议接到 runtime evidence seam。"""

    def __init__(self, *, evaluation_protocol: str) -> None:
        """固定本实例唯一允许读取的 OSWorld 状态协议。

        输入参数：
            evaluation_protocol：Chrome profile-name 或 Google Shopping
                active-tab 的版本化协议 ID。
        输出返回值：
            无；构造阶段不读取 task、VM 或浏览器状态。
        异常：
            UnsupportedTaskEvaluatorError：协议不是本 adapter 的固定闭集。
        """

        if evaluation_protocol not in {
            CHROME_PROFILE_NAME_PROTOCOL_ID,
            GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
        }:
            raise UnsupportedTaskEvaluatorError(
                "OSWorld state evaluation protocol 不受支持"
            )
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """读取冻结逐 VM 状态并产生 allowlist-only runtime 结果。

        输入参数：
            task：已由 registry 精确绑定的可信 canonical task。
            final_output：Agent 最终文本；状态协议不读取该值。
            environment：仍存活且提供 ``osworld_state_observations`` 的环境。
        输出返回值：
            二值 score，以及只含协议、原因码和计数的安全详情。
        异常：
            TypeError：环境缺少 evidence 接口或返回值不是 tuple。
            OSWorldStateEvaluationError：证据不完整；由 AttemptRunner 记
                ``EvaluationOutcome.ERROR`` 与 ``score=None``。
        """

        del task, final_output
        observation_reader = getattr(
            environment,
            "osworld_state_observations",
            None,
        )
        if not callable(observation_reader):
            raise TypeError("OSWorld environment 缺少 state observation 接口")
        observations = observation_reader(self._evaluation_protocol)
        if not isinstance(observations, tuple):
            raise TypeError("OSWorld state observations 必须是 tuple")

        if self._evaluation_protocol == CHROME_PROFILE_NAME_PROTOCOL_ID:
            evaluation = evaluate_chrome_profile_name_observations(
                observations,
                expected_name="Thomas",
            )
        else:
            evaluation = evaluate_google_shopping_active_tab_observations(observations)
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_osworld_state_details(evaluation),
        )


class OSWorldBookmarkTaskEvaluator:
    """把 Chrome Bookmarks 证据协议接到 runtime environment seam。"""

    def __init__(self, *, task_id: str, evaluation_protocol: str) -> None:
        """固定本实例唯一允许评价的 task 与书签协议。

        输入参数：
            task_id：必须命中不可变 Bookmark 规则目录的 canonical ID。
            evaluation_protocol：必须精确等于 Chrome Bookmarks v1 协议。
        输出返回值：
            无；构造阶段不读取 VM、书签或 Agent 文本。
        异常：
            UnsupportedTaskEvaluatorError：task 或协议不受支持。
        """

        if (
            evaluation_protocol != CHROME_BOOKMARKS_PROTOCOL_ID
            or task_id not in OSWORLD_BOOKMARK_TASK_RULES
        ):
            raise UnsupportedTaskEvaluatorError(
                "OSWorld bookmark evaluation protocol 不受支持"
            )
        self._task_id = task_id
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """读取冻结逐 VM 书签快照并返回 allowlist-only 结果。

        输入参数：
            task：已由 registry 精确绑定的可信 canonical task。
            final_output：Agent 最终文本；书签协议不读取该值。
            environment：仍存活且提供 bookmark observation seam 的环境。
        输出返回值：
            旧最终规则的覆盖分数，以及不含 URL、文件夹或标题的详情。
        异常：
            TypeError：task 身份漂移、环境接口或返回类型无效。
            OSWorldBookmarkEvaluationError：全部 VM 证据不完整；交由
                AttemptRunner 映射为 evaluation ERROR 与空 score。
        """

        del final_output
        if task.get("task_id") != self._task_id:
            raise TypeError("OSWorld bookmark task 身份漂移")
        observation_reader = getattr(
            environment,
            "osworld_bookmark_observations",
            None,
        )
        if not callable(observation_reader):
            raise TypeError("OSWorld environment 缺少 bookmark observation 接口")
        observations = observation_reader(
            self._task_id,
            self._evaluation_protocol,
        )
        if not isinstance(observations, tuple):
            raise TypeError("OSWorld bookmark observations 必须是 tuple")
        evaluation = evaluate_chrome_bookmark_observations(
            self._task_id,
            observations,
        )
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_osworld_bookmark_details(evaluation),
        )


class OSWorldArtifactStateTaskEvaluator:
    """把 15 个 OSWorld artifact-state 纯评价规则接到 runtime seam。"""

    def __init__(
        self,
        *,
        task_id: str,
        evaluation_protocol: str,
    ) -> None:
        """固定当前 adapter 唯一允许的 task 与协议身份。

        输入参数：
            task_id：必须存在于可信 artifact-state 规则目录的
                canonical task ID。
            evaluation_protocol：必须精确等于版本化 artifact-state
                协议 ID。
        输出返回值：
            无；构造阶段不读取客户机路径、artifact 或 gold。
        异常：
            UnsupportedTaskEvaluatorError：协议或 task 身份不在固定闭集。
        """

        if evaluation_protocol != ARTIFACT_STATE_PROTOCOL_ID:
            raise UnsupportedTaskEvaluatorError(
                "OSWorld artifact-state evaluation protocol 不受支持"
            )
        if (
            not isinstance(task_id, str)
            or task_id not in OSWORLD_ARTIFACT_STATE_TASK_RULES
        ):
            raise UnsupportedTaskEvaluatorError(
                "OSWorld artifact-state task 规则未注册"
            )
        self._task_id = task_id
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """读取冻结的逐 VM artifact observation 并产生脱敏结果。

        输入参数：
            task：必须与构造阶段固定规则精确匹配的可信
                canonical task。
            final_output：Agent 最终文本；artifact-state 评价不读取该值。
            environment：仍存活且显式提供
                ``osworld_artifact_state_observations(task_id, protocol_id)``
                的环境。
        输出返回值：
            原始合取 score 与仅含协议、规则、原因码和计数的
            ``RuntimeEvaluation``。
        异常：
            UnsupportedTaskEvaluatorError：task 在 registry 构造后被替换或篡改。
            TypeError：环境缺少证据接口，或返回值不是 tuple。
            OSWorldArtifactStateEvaluationError：读取、解析、schema 或闭集证据
                不可靠；异常由 AttemptRunner 记为 ERROR/null。
        """

        del final_output
        rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[self._task_id]
        if not _matches_osworld_artifact_task(task, rule):
            raise UnsupportedTaskEvaluatorError(
                "OSWorld artifact-state task contract 不匹配"
            )
        observation_reader = getattr(
            environment,
            "osworld_artifact_state_observations",
            None,
        )
        if not callable(observation_reader):
            raise TypeError("OSWorld environment 缺少 artifact-state observation 接口")
        observations = observation_reader(
            self._task_id,
            self._evaluation_protocol,
        )
        if not isinstance(observations, tuple):
            raise TypeError("OSWorld artifact-state observations 必须是 tuple")

        evaluation = evaluate_artifact_state_observations(
            self._task_id,
            observations,
        )
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_osworld_artifact_state_details(evaluation),
        )


class OperationTaskEvaluator:
    """把 32 个 Operation eval-rules 任务接到完整 artifact 快照。"""

    def __init__(
        self,
        *,
        task_id: str,
        evaluation_protocol: str,
    ) -> None:
        """固定当前 adapter 的任务与版本化协议身份。

        输入参数：
            task_id：必须存在于 32-task Operation 规则目录。
            evaluation_protocol：必须精确等于 Operation 协议 ID。
        输出返回值：
            无；构造阶段不读取 artifact、gold 或 Agent 文本。
        异常：
            UnsupportedTaskEvaluatorError：协议或 task 不在固定闭集。
        """

        if evaluation_protocol != OPERATION_PROTOCOL_ID:
            raise UnsupportedTaskEvaluatorError(
                "Operation evaluation protocol 不受支持"
            )
        if not isinstance(task_id, str) or task_id not in OPERATION_TASK_RULES:
            raise UnsupportedTaskEvaluatorError("Operation task 规则未注册")
        self._task_id = task_id
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """从存活环境取完整快照并执行 pure artifact 评价。

        输入参数：
            task：含完整 eval_rules 的可信 canonical task。
            final_output：Agent 最终文本；Operation 评价明确不读取。
            environment：必须提供
                ``operation_artifact_snapshot(task_id, protocol_id)``。
        输出返回值：
            pure evaluator 的分数与仅含协议、规则 ID、原因码和
            整数计数的 ``RuntimeEvaluation``。
        异常：
            UnsupportedTaskEvaluatorError：canonical task 身份漂移。
            TypeError/OperationArtifactCaptureError：证据接口、快照
                身份或完整性无效；AttemptRunner 会记为 ERROR/null。
        """

        del final_output
        if (
            task.get("task_id") != self._task_id
            or task.get("task_tag") != "FileOperate"
        ):
            raise UnsupportedTaskEvaluatorError("Operation task contract 不匹配")
        input_text_baseline = None
        if self._task_id in _WORD_TEXT_FIDELITY_TASK_IDS:
            baseline_reader = getattr(
                environment,
                "operation_word_text_baseline",
                None,
            )
            if not callable(baseline_reader):
                raise TypeError(
                    "OSWorld environment 缺少 Operation Word typed baseline 接口"
                )
            input_text_baseline = baseline_reader(
                self._task_id,
                self._evaluation_protocol,
            )
            if not isinstance(input_text_baseline, WordTextBaseline):
                raise TypeError("Operation Word typed baseline 类型无效")
            contract = operation_word_text_input_contract(self._task_id)
            if contract is None:
                raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID")
            document_paths = tuple(
                file.path
                for file in contract.files
                if file.path.casefold().endswith(".docx")
            )
            try:
                validate_word_text_baseline_identity(
                    input_text_baseline,
                    task_id=self._task_id,
                    protocol_id=self._evaluation_protocol,
                    manifest_sha256=contract.manifest_sha256,
                    document_paths=document_paths,
                )
            except WordTextFidelityError:
                raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID") from None
        input_abbreviation_baseline = None
        if self._task_id == _WORD_ABBREVIATION_TASK_ID:
            abbreviation_reader = getattr(
                environment,
                "operation_word_abbreviation_baseline",
                None,
            )
            if not callable(abbreviation_reader):
                raise TypeError(
                    "OSWorld environment 缺少 Operation Word "
                    "abbreviation typed baseline 接口"
                )
            input_abbreviation_baseline = abbreviation_reader(
                self._task_id,
                self._evaluation_protocol,
            )
            if not isinstance(
                input_abbreviation_baseline,
                WordAbbreviationBaseline,
            ):
                raise TypeError("Operation Word abbreviation typed baseline 类型无效")
            contract = operation_word_abbreviation_input_contract(self._task_id)
            if contract is None:
                raise OperationEvaluationError("WORD_ABBREVIATION_SEMANTICS_INVALID")
            document_paths = tuple(file.path for file in contract.files)
            try:
                validate_word_abbreviation_baseline_identity(
                    input_abbreviation_baseline,
                    task_id=self._task_id,
                    protocol_id=self._evaluation_protocol,
                    manifest_sha256=contract.manifest_sha256,
                    document_paths=document_paths,
                )
            except WordAbbreviationError:
                raise OperationEvaluationError(
                    "WORD_ABBREVIATION_SEMANTICS_INVALID"
                ) from None
        snapshot_reader = getattr(
            environment,
            "operation_artifact_snapshot",
            None,
        )
        if not callable(snapshot_reader):
            raise TypeError("OSWorld environment 缺少 Operation artifact 快照接口")
        snapshot = snapshot_reader(
            self._task_id,
            self._evaluation_protocol,
        )
        if not isinstance(snapshot, OperationArtifactSnapshot):
            raise TypeError("Operation artifact 快照类型无效")
        if (
            snapshot.task_id != self._task_id
            or snapshot.protocol_id != self._evaluation_protocol
        ):
            raise OperationArtifactCaptureError("Operation artifact 快照身份漂移")
        snapshot_root = snapshot.artifact_root()
        if (
            self._task_id in _WORD_TEXT_FIDELITY_TASK_IDS
            or self._task_id == _WORD_ABBREVIATION_TASK_ID
        ):
            try:
                # TemporaryDirectory 是 host runtime 刚创建的可信根；
                # 先消除 macOS /var -> /private/var 系统别名，再由
                # fidelity reader 对规范根的每一级执行 nofollow。
                snapshot_root = snapshot_root.resolve(strict=True)
            except (OSError, RuntimeError):
                raise OperationArtifactCaptureError(
                    "Operation artifact 快照根无效"
                ) from None
        evaluation = evaluate_operation_artifacts(
            snapshot_root,
            task,
            input_text_baseline=input_text_baseline,
            input_abbreviation_baseline=input_abbreviation_baseline,
        )
        if snapshot.file_count != evaluation.artifact_count:
            raise OperationArtifactCaptureError("Operation artifact 快照不完整")
        return RuntimeEvaluation(
            passed=evaluation.passed,
            score=evaluation.score,
            details=_operation_details(evaluation),
        )


class PipelineImplicitTaskEvaluator:
    """把固定 pipeline-implicit typed observation 接到纯评价协议。"""

    def __init__(self, *, task_id: str, evaluation_protocol: str) -> None:
        """固定当前 adapter 唯一允许的 canonical task 与协议。

        输入参数：
            task_id：必须命中专属 pipeline-implicit 任务绑定闭集。
            evaluation_protocol：必须与该任务唯一协议精确相等。
        输出返回值：
            无；构造阶段不读取 Agent 文本、artifact、guest 或 gold。
        异常：
            UnsupportedTaskEvaluatorError：任务或协议不属于固定绑定。
        """

        binding = _PIPELINE_IMPLICIT_TASK_BINDINGS.get(task_id)
        if binding is None or binding["protocol_id"] != evaluation_protocol:
            raise UnsupportedTaskEvaluatorError(
                "pipeline-implicit evaluation protocol 不受支持"
            )
        self._task_id = task_id
        self._evaluation_protocol = evaluation_protocol

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """只读取环境冻结的 typed observation 并返回脱敏结果。

        输入参数：
            task：必须仍精确匹配构造时固定的 canonical metadata。
            final_output：Agent 最终文本；四个 artifact 协议均明确忽略。
            environment：必须提供 ``pipeline_implicit_observation`` seam。
        输出返回值：
            纯评价器分数与不含路径、内容、哈希、类别或单元格的详情。
        异常：
            UnsupportedTaskEvaluatorError：task metadata 漂移。
            TypeError：environment seam 或 observation 类型无效；由
                AttemptRunner 映射为 ERROR/null。
        """

        del final_output
        if not _matches_pipeline_implicit_task(
            task,
            task_id=self._task_id,
            evaluation_protocol=self._evaluation_protocol,
        ):
            raise UnsupportedTaskEvaluatorError(
                "pipeline-implicit task contract 不匹配"
            )
        reader = getattr(environment, "pipeline_implicit_observation", None)
        if not callable(reader):
            raise TypeError(
                "OSWorld environment 缺少 pipeline-implicit observation 接口"
            )
        observation = reader(self._task_id, self._evaluation_protocol)
        if self._evaluation_protocol == CROSS_DOCUMENT_PROTOCOL_ID:
            if not isinstance(observation, CrossDocumentObservation):
                raise TypeError("pipeline-implicit observation 类型无效")
            evaluation = evaluate_cross_document(observation)
            return RuntimeEvaluation(
                passed=evaluation.passed,
                score=evaluation.score,
                details=_cross_document_details(evaluation),
            )
        if self._evaluation_protocol == HIDE_NA_ROWS_PROTOCOL_ID:
            if not isinstance(observation, HideNARowsObservation):
                raise TypeError("pipeline-implicit observation 类型无效")
            evaluation = evaluate_hide_na_rows(observation)
            return RuntimeEvaluation(
                passed=evaluation.passed,
                score=evaluation.score,
                details=_hide_na_rows_details(evaluation),
            )
        if self._evaluation_protocol == IMAGE_CLASSIFICATION_PROTOCOL_ID:
            if not isinstance(observation, ImageClassificationObservation):
                raise TypeError("pipeline-implicit observation 类型无效")
            evaluation = evaluate_image_classification(observation)
            return RuntimeEvaluation(
                passed=evaluation.passed,
                score=evaluation.score,
                details=_image_classification_details(evaluation),
            )
        if self._evaluation_protocol == SEARCHWRITE_XLSX_PROTOCOL_ID:
            if not isinstance(observation, SearchWriteObservation):
                raise TypeError("pipeline-implicit observation 类型无效")
            evaluation = evaluate_searchwrite_xlsx(observation)
            return RuntimeEvaluation(
                passed=evaluation.passed,
                score=evaluation.score,
                details=_searchwrite_xlsx_details(evaluation),
            )
        raise UnsupportedTaskEvaluatorError(
            "pipeline-implicit evaluation protocol 不受支持"
        )


def _checkout_profile_from_task(
    task: Mapping[str, Any],
) -> Mapping[str, object]:
    """只从 PreparedTask 的可信 fixture 投影取出 checkout profile。

    输入参数：
        task：evaluator 可见的 trusted task；原始 canonical JSON 只含
            fixture 引用，准备层才会注入 ``resolved_fixtures``。
    输出返回值：
        synthetic checkout fixture 的 ``profile`` 映射；不复制到 details。
    异常：
        ValueError：运行时绕过 PreparedTask，或 fixture/profile 投影缺失。
    """

    resolved = task.get("resolved_fixtures")
    if not isinstance(resolved, Mapping):
        raise ValueError("WebMall task 缺少可信 checkout fixture")
    fixture = resolved.get("checkout_profile")
    if not isinstance(fixture, Mapping):
        raise ValueError("WebMall checkout fixture 投影无效")
    profile = fixture.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("WebMall checkout profile 投影无效")
    return profile


def _checkout_details(
    evaluation: CheckoutEvaluation,
) -> dict[str, Any]:
    """把 checkout core 结果投影为不含外部身份和值的详情。

    输入参数：
        evaluation：纯 checkout evaluator 的不可变结果。
    输出返回值：
        只含固定协议/reason code 和整数计数的 RunStore 安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "expected_order_count": evaluation.expected_order_count,
        "observed_order_count": evaluation.observed_order_count,
        "duplicate_observation_count": (evaluation.duplicate_observation_count),
        "missing_order_count": evaluation.missing_order_count,
        "unexpected_order_count": evaluation.unexpected_order_count,
        "product_mismatch_order_count": (evaluation.product_mismatch_order_count),
        "checkout_state_mismatch_order_count": (
            evaluation.checkout_state_mismatch_order_count
        ),
        "payment_mismatch_order_count": (evaluation.payment_mismatch_order_count),
        "billing_profile_mismatch_order_count": (
            evaluation.billing_profile_mismatch_order_count
        ),
    }


def _find_and_order_details(
    evaluation: FindAndOrderEvaluation,
) -> dict[str, Any]:
    """把报告 URL AND checkout 结果投影为安全计数详情。

    输入参数：
        evaluation：纯 FindAndOrder 组合 evaluator 结果。
    输出返回值：
        不含 URL、slug、订单身份、profile 或 endpoint 的映射。
    """

    checkout_details = _checkout_details(evaluation.checkout)
    checkout_details.pop("protocol_id")
    checkout_details.pop("reason_codes")
    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "reported_url_mismatch_count": (evaluation.reported_url_mismatch_count),
        "checkout_passed": evaluation.checkout.passed,
        **checkout_details,
    }


def _webmall_url_set_details(
    evaluation: WebMallURLSetEvaluation,
) -> dict[str, Any]:
    """把 URL 多集合结果投影为不含 URL 的安全诊断。

    输入参数：
        evaluation：pure URL-multiset evaluator 的不可变结果。
    输出返回值：
        只含 protocol ID、matched/wrong/missing 计数与三个
        比例的映射；不复制 URL、host 或报告文本。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "matched_count": len(evaluation.matched),
        "wrong_count": len(evaluation.wrong),
        "missing_count": len(evaluation.missing),
        "precision": evaluation.precision,
        "recall": evaluation.recall,
        "f1": evaluation.f1,
    }


def _webmall_cart_details(
    evaluation: CartEvaluation,
) -> dict[str, Any]:
    """把 Cart 评价投影为 RunStore 允许持久化的计数闭集。

    输入参数：evaluation 为 pure Cart evaluator 的不可变结果。
    输出返回值：不含 URL、slug、worker/store identity、Cart 内容或 gold
        的协议、reason code 与整数计数字典。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "evaluated_worker_count": evaluation.evaluated_worker_count,
        "expected_product_quantity": evaluation.expected_product_quantity,
        "observed_product_quantity": evaluation.observed_product_quantity,
        "matched_product_quantity": evaluation.matched_product_quantity,
        "missing_product_quantity": evaluation.missing_product_quantity,
        "unexpected_product_quantity": evaluation.unexpected_product_quantity,
        "quantity_mismatch_identity_count": (
            evaluation.quantity_mismatch_identity_count
        ),
        "nonselected_worker_product_quantity": (
            evaluation.nonselected_worker_product_quantity
        ),
    }


def _osworld_state_details(
    evaluation: OSWorldStateEvaluation,
) -> dict[str, Any]:
    """把 OSWorld 状态结果投影为不含页面或 profile 原值的详情。

    输入参数：
        evaluation：纯 profile/active-tab evaluator 的不可变结果。
    输出返回值：
        只含固定协议、原因码与整数计数的 RunStore 安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "evaluated_vm_count": evaluation.evaluated_vm_count,
        "evaluator_error_vm_count": evaluation.evaluator_error_vm_count,
        "missing_state_count": evaluation.missing_state_count,
        "unexpected_state_count": evaluation.unexpected_state_count,
    }


def _osworld_bookmark_details(
    evaluation: OSWorldBookmarkEvaluation,
) -> dict[str, Any]:
    """把书签评价结果投影为不含 URL、标题或文件夹的详情。

    输入参数：
        evaluation：纯 Bookmark evaluator 的不可变公开结果。
    输出返回值：
        只含固定协议、规则身份、原因码与整数计数的安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "task_rule_id": evaluation.task_rule_id,
        "reason_codes": evaluation.reason_codes,
        "evaluated_vm_count": evaluation.evaluated_vm_count,
        "evaluator_error_vm_count": evaluation.evaluator_error_vm_count,
        "expected_target_count": evaluation.expected_target_count,
        "matched_target_count": evaluation.matched_target_count,
    }


def _osworld_artifact_state_details(
    evaluation: OSWorldArtifactStateEvaluation,
) -> dict[str, Any]:
    """把 artifact-state 结果投影为不含路径、内容或 gold 的详情。

    输入参数：
        evaluation：纯 artifact-state evaluator 的不可变结果。
    输出返回值：
        只含固定协议/规则 ID、原因码与整数计数的 RunStore
        安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "task_rule_id": evaluation.task_rule_id,
        "reason_codes": evaluation.reason_codes,
        "evaluated_vm_count": evaluation.evaluated_vm_count,
        "evaluator_error_vm_count": evaluation.evaluator_error_vm_count,
        "missing_artifact_count": evaluation.missing_artifact_count,
        "failed_metric_count": evaluation.failed_metric_count,
    }


def _operation_details(
    evaluation: OperationEvaluation,
) -> dict[str, Any]:
    """把 Operation pure evaluator 结果投影为严格脱敏详情。

    输入参数：
        evaluation：只在 evaluator 内存中使用的不可变结果。
    输出返回值：
        仅含固定协议、规则 ID、原因码和整数计数的映射；
        不含文件名、路径、内容、gold、检查参数或逐规则结果。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "task_rule_id": evaluation.task_rule_id,
        "reason_codes": evaluation.reason_codes,
        "evaluated_rule_count": evaluation.evaluated_rule_count,
        "passed_rule_count": evaluation.passed_rule_count,
        "failed_rule_count": evaluation.failed_rule_count,
        "artifact_count": evaluation.artifact_count,
    }


def _image_classification_details(
    evaluation: ImageClassificationEvaluation,
) -> dict[str, Any]:
    """把图片分类结果投影为不含类别、摘要或文件身份的详情。

    输入参数：
        evaluation：PPT-003 纯评价器返回的不可变结果。
    输出返回值：
        仅含固定协议、reason code 与整数计数的 RunStore 安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "expected_category_count": evaluation.expected_category_count,
        "matched_category_count": evaluation.matched_category_count,
        "unexpected_category_count": evaluation.unexpected_category_count,
        "expected_classification_count": (evaluation.expected_classification_count),
        "matched_classification_count": (evaluation.matched_classification_count),
        "missing_classification_count": (evaluation.missing_classification_count),
        "misclassified_image_count": evaluation.misclassified_image_count,
        "duplicate_classification_count": (evaluation.duplicate_classification_count),
        "unexpected_image_count": evaluation.unexpected_image_count,
        "missing_unclassified_image_count": (
            evaluation.missing_unclassified_image_count
        ),
        "duplicate_source_image_count": (evaluation.duplicate_source_image_count),
        "changed_presentation_count": evaluation.changed_presentation_count,
        "unexpected_regular_file_count": (evaluation.unexpected_regular_file_count),
    }


def _hide_na_rows_details(
    evaluation: HideNARowsEvaluation,
) -> dict[str, Any]:
    """把隐藏行结果投影为不含工作簿名称或行身份的详情。

    输入参数：
        evaluation：Excel-008 纯评价器返回的不可变结果。
    输出返回值：
        仅含固定协议、reason code 和文档/行计数的安全映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "expected_document_count": evaluation.expected_document_count,
        "evaluated_document_count": evaluation.evaluated_document_count,
        "unexpected_document_count": evaluation.unexpected_document_count,
        "expected_hidden_row_count": evaluation.expected_hidden_row_count,
        "matched_hidden_row_count": evaluation.matched_hidden_row_count,
        "missing_hidden_row_count": evaluation.missing_hidden_row_count,
        "unexpected_hidden_row_count": (evaluation.unexpected_hidden_row_count),
        "mutated_document_count": evaluation.mutated_document_count,
    }


def _searchwrite_xlsx_details(
    evaluation: SearchWriteEvaluation,
) -> dict[str, Any]:
    """把 SearchAndWrite 结果投影为不含单元格身份或值的详情。

    输入参数：
        evaluation：SearchAndWrite-008 纯评价器的不可变结果。
    输出返回值：
        固定协议、reason code 与文档/单元格/基线计数映射。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "expected_document_count": evaluation.expected_document_count,
        "evaluated_document_count": evaluation.evaluated_document_count,
        "unexpected_document_count": evaluation.unexpected_document_count,
        "expected_cell_count": evaluation.expected_cell_count,
        "matched_cell_count": evaluation.matched_cell_count,
        "missing_cell_count": evaluation.missing_cell_count,
        "mismatched_cell_count": evaluation.mismatched_cell_count,
        "unexpected_cell_count": evaluation.unexpected_cell_count,
        "mutated_document_count": evaluation.mutated_document_count,
    }


def _cross_document_details(
    evaluation: CrossDocumentEvaluation,
) -> dict[str, Any]:
    """把跨文档事实结果投影为不含月份、数值或正文的详情。

    输入参数：
        evaluation：CombinationDocs-002 纯评价器返回的不可变结果。
    输出返回值：
        固定协议、reason code、事实/文档/完整性计数与事实源布尔状态。
    """

    return {
        "protocol_id": evaluation.protocol_id,
        "reason_codes": evaluation.reason_codes,
        "required_fact_count": evaluation.required_fact_count,
        "matched_fact_count": evaluation.matched_fact_count,
        "failed_fact_count": evaluation.failed_fact_count,
        "missing_document_count": evaluation.missing_document_count,
        "unexpected_document_count": evaluation.unexpected_document_count,
        "semantic_integrity_failure_count": (
            evaluation.semantic_integrity_failure_count
        ),
        "reference_spreadsheet_changed": (evaluation.reference_spreadsheet_changed),
    }


def _matches_pipeline_implicit_task(
    task: Mapping[str, Any],
    *,
    task_id: str,
    evaluation_protocol: str,
) -> bool:
    """验证 canonical task 仍精确匹配专属任务/协议绑定。

    输入参数：
        task：PreparedTask 保留的可信 canonical metadata。
        task_id/evaluation_protocol：adapter 构造时固定的唯一身份。
    输出返回值：
        task ID、UID、类型、来源、标签、evaluator path 与协议均未漂移
        时返回 ``True``；不读取 instruction、answer 或 Agent 输出。
    """

    binding = _PIPELINE_IMPLICIT_TASK_BINDINGS.get(task_id)
    if (
        binding is None
        or binding["protocol_id"] != evaluation_protocol
        or task.get("task_id") != task_id
    ):
        return False
    return all(
        task.get(field) == expected
        for field, expected in binding.items()
        if field != "protocol_id"
    )


def _matches_osworld_artifact_task(
    task: Mapping[str, Any],
    rule: ArtifactStateTaskRule,
) -> bool:
    """验证 runtime task 仍精确绑定规则目录中的源 evaluator。

    输入参数：
        task：PreparedTask 保留的可信 canonical metadata。
        rule：根据 task ID 从不可变目录取得的 artifact-state rule。
    输出返回值：
        task ID、来源、类型与 evaluator path 全部精确匹配时返回
        ``True``，否则返回 ``False``；不读取答案或任务内容。
    """

    return (
        task.get("task_id") == rule.task_id
        and task.get("task_source") == "OSWorld"
        and task.get("task_type") == "OSWorld脚本"
        and task.get("evaluator_path")
        == f"eval/osworld_scripts/{rule.source_evaluator_id}.json"
    )


def _matches_webmall_cart_task(task: Mapping[str, Any]) -> bool:
    """验证 runtime task 仍位于 8 个已审计 Cart 身份闭集。

    输入参数：task 为 PreparedTask 保留的可信 canonical metadata。
    输出返回值：ID 对应 tag、来源、类型、答案类型与 evaluator 路径全部
        精确匹配时返回 ``True``；不读取 instruction、gold 或 Agent 输出。
    """

    task_id = task.get("task_id")
    expected_tag = (
        _WEBMALL_CART_TASK_TAGS.get(task_id) if isinstance(task_id, str) else None
    )
    return (
        expected_tag is not None
        and task.get("task_source") == "WebMall"
        and task.get("task_type") == "QA"
        and task.get("task_tag") == expected_tag
        and task.get("answer_type") == "cart"
        and task.get("evaluator_path") == "evaluators/cart_evaluator.py"
    )


def _matches_osworld_bookmark_task(
    task: Mapping[str, Any],
    binding: BookmarkTaskBinding,
) -> bool:
    """验证 runtime task 精确匹配共享 Bookmark 身份目录。

    输入参数：
        task：PreparedTask 保留的可信 canonical metadata。
        binding：根据 task ID 从不可变共享目录取得的正式绑定。
    输出返回值：
        ID、UID、来源、类型、标签与 evaluator path 全部一致时返回
        ``True``；不读取 answer、instruction 或 Agent 输出。
    """

    return all(
        task.get(field) == getattr(binding, field)
        for field in (
            "task_id",
            "task_uid",
            "task_source",
            "task_type",
            "task_tag",
            "evaluator_path",
        )
    )


def build_task_evaluator(
    task: Mapping[str, Any],
    *,
    evaluation_protocol: str,
) -> (
    AnswerTaskEvaluator
    | WebMallCheckoutTaskEvaluator
    | WebMallURLSetTaskEvaluator
    | WebMallCartTaskEvaluator
    | OSWorldStateTaskEvaluator
    | OSWorldBookmarkTaskEvaluator
    | OSWorldArtifactStateTaskEvaluator
    | OperationTaskEvaluator
    | PipelineImplicitTaskEvaluator
):
    """按 canonical task contract 选择已迁移的 runtime evaluator。

    输入参数：
        task：可信 canonical task；registry 只读取类型与来源标识，不读取或
            记录答案值。
        evaluation_protocol：runtime-support 固定并已经过版本格式校验的
            evaluator 协议 ID；registry 必须按它选择实际实现。
    输出返回值：
        当前 78 个非 WebMall QA 共用的 ``AnswerTaskEvaluator``，按
        Checkout/EndToEnd 任务标签严格绑定的 WebMall closed-world
        adapter，两个显式 OSWorld Chrome 状态 adapter，已固定
        规则目录的 15 个 artifact-state adapter，或 32 个
        Operation pure artifact adapter。
    异常：
        TypeError：task 不是 Mapping。
        UnsupportedTaskEvaluatorError：任务仍需尚未迁移的状态评价协议。
    """

    if not isinstance(task, Mapping):
        raise TypeError("task 必须是 Mapping")
    if not isinstance(evaluation_protocol, str):
        raise TypeError("evaluation protocol 必须是字符串")
    if (
        evaluation_protocol in _NATIVE_ANSWER_PROTOCOLS
        and task.get("task_type") == "QA"
        and task.get("task_source") != "WebMall"
    ):
        return AnswerTaskEvaluator()
    expected_webmall_protocol = {
        "Checkout": CHECKOUT_PROTOCOL_ID,
        "EndToEnd": FIND_AND_ORDER_PROTOCOL_ID,
    }.get(task.get("task_tag"))
    if (
        task.get("task_type") == "QA"
        and task.get("task_source") == "WebMall"
        and expected_webmall_protocol == evaluation_protocol
    ):
        return WebMallCheckoutTaskEvaluator(evaluation_protocol=evaluation_protocol)
    if (
        evaluation_protocol == URL_MULTISET_PROTOCOL_ID
        and task.get("task_type") == "QA"
        and task.get("task_source") == "WebMall"
        and task.get("evaluator_path") == "evaluators/string_url_evaluator.py"
    ):
        return WebMallURLSetTaskEvaluator()
    if evaluation_protocol == CART_PROTOCOL_ID and _matches_webmall_cart_task(task):
        cart_task_id = task.get("task_id")
        if isinstance(cart_task_id, str):
            return WebMallCartTaskEvaluator(
                task_id=cart_task_id,
                evaluation_protocol=evaluation_protocol,
            )
    osworld_binding = {
        CHROME_PROFILE_NAME_PROTOCOL_ID: {
            "task_id": "Operation-WebOperate-Settings-001",
            "task_source": "OSWorld",
            "task_tag": "WebOperate",
            "evaluation_mode": "osworld_profile_state",
            "profile_state_adapter": "chrome_profile_name_v1",
            "vm_aggregation": "any_complete",
        },
        GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID: {
            "task_id": "Operation-WebOperate-WebNavigate-009",
            "task_source": "OSWorld",
            "task_tag": "WebOperate",
            "evaluation_mode": "osworld_active_tab",
            "active_tab_adapter": ("google_shopping_selected_filters_v1"),
            "vm_aggregation": "any_complete",
        },
    }.get(evaluation_protocol)
    if osworld_binding is not None and all(
        task.get(field) == value for field, value in osworld_binding.items()
    ):
        return OSWorldStateTaskEvaluator(evaluation_protocol=evaluation_protocol)
    if evaluation_protocol == CHROME_BOOKMARKS_PROTOCOL_ID:
        bookmark_task_id = task.get("task_id")
        bookmark_binding = (
            OSWORLD_BOOKMARK_TASK_BINDINGS.get(bookmark_task_id)
            if isinstance(bookmark_task_id, str)
            else None
        )
        if bookmark_binding is not None and _matches_osworld_bookmark_task(
            task,
            bookmark_binding,
        ):
            return OSWorldBookmarkTaskEvaluator(
                task_id=bookmark_binding.task_id,
                evaluation_protocol=evaluation_protocol,
            )
    if evaluation_protocol == ARTIFACT_STATE_PROTOCOL_ID:
        artifact_task_id = task.get("task_id")
        artifact_rule = (
            OSWORLD_ARTIFACT_STATE_TASK_RULES.get(artifact_task_id)
            if isinstance(artifact_task_id, str)
            else None
        )
        if artifact_rule is not None and _matches_osworld_artifact_task(
            task,
            artifact_rule,
        ):
            return OSWorldArtifactStateTaskEvaluator(
                task_id=artifact_rule.task_id,
                evaluation_protocol=evaluation_protocol,
            )
    if evaluation_protocol == OPERATION_PROTOCOL_ID:
        operation_task_id = task.get("task_id")
        if (
            isinstance(operation_task_id, str)
            and operation_task_id in OPERATION_TASK_RULES
            and task.get("task_tag") == "FileOperate"
        ):
            return OperationTaskEvaluator(
                task_id=operation_task_id,
                evaluation_protocol=evaluation_protocol,
            )
    pipeline_task_id = task.get("task_id")
    if isinstance(pipeline_task_id, str) and _matches_pipeline_implicit_task(
        task,
        task_id=pipeline_task_id,
        evaluation_protocol=evaluation_protocol,
    ):
        return PipelineImplicitTaskEvaluator(
            task_id=pipeline_task_id,
            evaluation_protocol=evaluation_protocol,
        )
    raise UnsupportedTaskEvaluatorError(
        "evaluation protocol 尚未迁移到 runtime registry"
    )
