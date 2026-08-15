"""Operation eval-rules 原生纯 artifact 评价协议。"""

from .catalog import (
    OPERATION_PROTOCOL_ID,
    OPERATION_TASK_RULES,
    OperationTaskRule,
)
from .evaluator import (
    OPERATION_CHECK_CONTRACTS,
    OperationCheckContract,
    OperationEvaluation,
    OperationEvaluationError,
    OperationPinnedArtifactContract,
    OperationPinnedInputFile,
    OperationRuleEvaluation,
    evaluate_operation_artifacts,
    operation_word_abbreviation_input_contract,
    operation_word_text_input_contract,
)
from .word_abbreviation_semantics import (
    WordAbbreviationBaseline,
    WordAbbreviationError,
    WordAbbreviationResult,
    capture_word_abbreviation_baseline,
    compare_word_abbreviation_semantics,
    validate_word_abbreviation_baseline_identity,
)
from .word_text_fidelity import (
    WordTextBaseline,
    WordTextFidelityError,
    WordTextFidelityResult,
    WordTextInputFile,
    capture_word_text_baseline,
    compare_word_text_fidelity,
    validate_word_text_baseline_identity,
)

__all__ = [
    "OPERATION_PROTOCOL_ID",
    "OPERATION_TASK_RULES",
    "OperationTaskRule",
    "OPERATION_CHECK_CONTRACTS",
    "OperationCheckContract",
    "OperationEvaluation",
    "OperationEvaluationError",
    "OperationPinnedArtifactContract",
    "OperationPinnedInputFile",
    "OperationRuleEvaluation",
    "evaluate_operation_artifacts",
    "operation_word_abbreviation_input_contract",
    "operation_word_text_input_contract",
    "WordAbbreviationBaseline",
    "WordAbbreviationError",
    "WordAbbreviationResult",
    "capture_word_abbreviation_baseline",
    "compare_word_abbreviation_semantics",
    "validate_word_abbreviation_baseline_identity",
    "WordTextBaseline",
    "WordTextFidelityError",
    "WordTextFidelityResult",
    "WordTextInputFile",
    "capture_word_text_baseline",
    "compare_word_text_fidelity",
    "validate_word_text_baseline_identity",
]
