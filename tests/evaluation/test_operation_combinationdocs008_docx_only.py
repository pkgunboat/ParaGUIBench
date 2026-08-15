"""CombinationDocs-008 DOCX-only 重命名合同的专属回归测试。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import zipfile

from paraguibench.benchmark import build_agent_task_view
from paraguibench.evaluation.operation import evaluate_operation_artifacts
from paraguibench.evaluation.operation.checks.file import check_named_files_exist


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_PATH = (
    _REPO_ROOT / "benchmark/tasks/Operation-FileOperate-CombinationDocs-008.json"
)
_ASSET_MANIFEST_PATH = (
    _REPO_ROOT
    / "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-008.json"
)


def _load_task() -> dict[str, object]:
    """读取 CombinationDocs-008 canonical task。

    输入参数：
        无；固定读取仓库中的 008 任务 JSON。
    输出返回值：
        解析后的任务映射，仅供当前专属测试使用。
    """

    return json.loads(_TASK_PATH.read_text(encoding="utf-8"))


def _identity(path: Path) -> tuple[int, str]:
    """返回测试文件的精确字节身份。

    输入参数：
        path：已写入隔离测试目录的普通文件。
    输出返回值：
        ``(size, sha256)`` 二元组，用于构造合成重命名合同。
    """

    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


def _write_docx(path: Path, text: str) -> None:
    """写入可被 Office 解析的最小合成 DOCX。

    输入参数：
        path：目标文档路径；text：唯一正文段落。
    输出返回值：
        无；通过 python-docx 保存合法 OOXML 包。
    """

    docx = __import__("docx")
    document = docx.Document()
    document.add_paragraph(text)
    document.save(path)


def _write_minimal_xlsx(path: Path) -> None:
    """写入仅供闭集合同保真的合成 XLSX 容器。

    输入参数：
        path：根目录中应保持不变的测试工作簿路径。
    输出返回值：
        无；写入包含必需 member 的非空 ZIP 容器。
    """

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("xl/workbook.xml", b"<workbook/>")


def _prepare_valid_rename(root: Path) -> dict[str, object]:
    """构造三份 DOCX 原子重命名后的合成 artifact 树。

    输入参数：
        root：pytest 提供的隔离 artifact 根目录。
    输出返回值：
        与合成输入字节精确一致的 canonical 规则参数副本。
    """

    params = deepcopy(_load_task()["eval_rules"][0]["params"])
    contract = params["rename_contract"]
    output = root / params["output_directory"]
    output.mkdir()
    for index, document_spec in enumerate(contract["documents"], start=1):
        source = root / document_spec["source_filename"]
        _write_docx(source, f"unchanged-source-content-{index}")
        source_size, source_sha256 = _identity(source)
        document_spec["source_size"] = source_size
        document_spec["source_sha256"] = source_sha256
        source.rename(output / document_spec["output_filename"])
    for preserved_spec in contract["preserved_files"]:
        path = root / preserved_spec["path"]
        if path.suffix == ".xlsx":
            _write_minimal_xlsx(path)
        else:
            path.write_text("synthetic naming rules", encoding="utf-8")
        size, sha256 = _identity(path)
        preserved_spec["size"] = size
        preserved_spec["sha256"] = sha256
    return params


def test_canonical_task_is_three_docx_outputs_with_single_rev_prefix() -> None:
    """验证正式任务只重命名三份 DOCX 且明确单个 rev。

    输入参数：
        无；读取 canonical task 中的 instruction 与唯一规则。
    输出返回值：
        无；任一 PPT 输出、``revv``、错误分母或非 ``output``
        目录合同均使测试失败。
    """

    task = _load_task()
    instruction = task["instruction"]
    params = task["eval_rules"][0]["params"]
    filenames = params["filenames"]

    assert isinstance(instruction, str)
    assert "Word (.docx) files only" in instruction
    assert "rename the Word and PPT files" not in instruction
    assert "Treat the conflicting examples in Naming_rules.txt as stale" in instruction
    assert "the .pptx reference does not apply" in instruction
    assert "Keep Project_Information.xlsx and Naming_rules.txt unchanged" in instruction
    assert params["output_directory"] == "output"
    assert params["expected_document_count"] == 3
    assert filenames == [
        "p-2026-001_multi_modal_agent_rev1.0.docx",
        "p-2026-002_gui_benchmark_study_rev2.1.docx",
        "p-2026-003_parallel_execution_rev3.5.docx",
    ]
    assert all(name.endswith(".docx") for name in filenames)
    assert all("revv" not in name for name in filenames)


def test_rename_contract_identities_are_derived_from_the_pinned_input_manifest() -> (
    None
):
    """验证输出保真摘要逐条来自正式五文件 input manifest。

    输入参数：
        无；读取 canonical task 与其 ``asset_manifest`` 引用。
    输出返回值：
        无；三份源 DOCX 及 XLSX/TXT 的 path、size、SHA-256
        任一脱离固定输入就失败。
    """

    task = _load_task()
    manifest = json.loads(_ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert (
        task["asset_manifest"]
        == _ASSET_MANIFEST_PATH.relative_to(_REPO_ROOT).as_posix()
    )
    manifest_identities = {
        row["path"]: (row["size"], row["sha256"]) for row in manifest["files"]
    }
    contract = task["eval_rules"][0]["params"]["rename_contract"]
    document_identities = {
        row["source_filename"]: (row["source_size"], row["source_sha256"])
        for row in contract["documents"]
    }
    preserved_identities = {
        row["path"]: (row["size"], row["sha256"]) for row in contract["preserved_files"]
    }

    assert document_identities | preserved_identities == manifest_identities
    assert set(document_identities) == {
        "GUI Benchmark Study.docx",
        "Multi Modal Agent.docx",
        "Parallel Execution.docx",
    }
    assert set(preserved_identities) == {
        "Naming_rules.txt",
        "Project_Information.xlsx",
    }


def test_agent_view_exposes_instruction_but_not_rename_contract_or_hashes() -> None:
    """验证 Agent 只看到修正后指令，看不到 evaluator 合同。

    输入参数：
        无；将完整 canonical 008 任务交给正式 Agent view builder。
    输出返回值：
        无；投影保留 DOCX-only 指令，但不含 eval rules、source
        size/SHA-256、manifest 路径或任何 rename-contract 字段。
    """

    task = _load_task()
    view = build_agent_task_view(task)
    serialized = json.dumps(view, ensure_ascii=False, sort_keys=True)

    assert view["instruction"] == task["instruction"]
    assert "eval_rules" not in view
    assert "asset_manifest" not in view
    assert "rename_contract" not in serialized
    assert "source_sha256" not in serialized


def test_exact_three_document_rename_contract_passes_with_fixed_denominator(
    tmp_path: Path,
) -> None:
    """验证三份文档仅重命名和移动后以固定分母通过。

    输入参数：
        tmp_path：包含根级元数据与 ``output`` 三文档的隔离目录。
    输出返回值：
        无；文件闭集、字节身份、OOXML 与目录全部正确时
        必须满分，且评价分母恒为三份文档。
    """

    params = _prepare_valid_rename(tmp_path)

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is True
    assert result["score"] == 1.0
    assert result["_evaluated_artifact_count"] == 3


def test_canonical_evaluator_keeps_three_document_denominator_when_missing(
    tmp_path: Path,
) -> None:
    """验证正式 evaluator 在输出全缺失时仍固定三文档分母。

    输入参数：
        tmp_path：不含任何 Agent artifact 的隔离根目录。
    输出返回值：
        无；canonical 规则必须返回零分且唯一规则的
        ``evaluated_artifact_count`` 恒为 3，不能被实际零文件缩小。
    """

    evaluation = evaluate_operation_artifacts(tmp_path, _load_task())

    assert evaluation.passed is False
    assert evaluation.score == 0.0
    assert evaluation.artifact_count == 0
    assert evaluation.rule_results[0].evaluated_artifact_count == 3


def test_missing_renamed_document_fails_without_shrinking_denominator(
    tmp_path: Path,
) -> None:
    """验证缺少任一目标 DOCX 时严格失败且分母不缩小。

    输入参数：
        tmp_path：先构造完整合成重命名树，再删除其中一份输出。
    输出返回值：
        无；结果必须为零分，且内部分母仍为 3。
    """

    params = _prepare_valid_rename(tmp_path)
    missing_name = params["filenames"][0]
    (tmp_path / "output" / missing_name).unlink()

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0
    assert result["_evaluated_artifact_count"] == 3


def test_extra_pptx_is_rejected_by_the_docx_only_closed_set(tmp_path: Path) -> None:
    """验证额外 PPTX 不能被 DOCX-only 任务静默接受。

    输入参数：
        tmp_path：完整合成终态之外再添加一份 PPTX 文件。
    输出返回值：
        无；任何额外文件都使整个闭集严格零分。
    """

    params = _prepare_valid_rename(tmp_path)
    (tmp_path / "unexpected-repair.pptx").write_bytes(b"synthetic-extra")

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0
    assert result["_evaluated_artifact_count"] == 3


def test_legacy_source_name_is_rejected_even_when_bytes_are_unchanged(
    tmp_path: Path,
) -> None:
    """验证保留任一旧 DOCX 名称不能通过字节保真门。

    输入参数：
        tmp_path：先生成正确终态，再把第一份输出原子移回旧名。
    输出返回值：
        无；即使文件字节完全不变，旧名仍必须使规则零分。
    """

    params = _prepare_valid_rename(tmp_path)
    first_document = params["rename_contract"]["documents"][0]
    (tmp_path / "output" / first_document["output_filename"]).rename(
        tmp_path / first_document["source_filename"]
    )

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_correctly_named_document_in_root_instead_of_output_is_rejected(
    tmp_path: Path,
) -> None:
    """验证正确新名但错误目录位置不能通过。

    输入参数：
        tmp_path：将完整终态中的一份新名 DOCX 从 ``output``
            移到 artifact 根目录。
    输出返回值：
        无；路径合同必须严格拒绝根级输出，不仅校验 basename。
    """

    params = _prepare_valid_rename(tmp_path)
    output_name = params["filenames"][0]
    (tmp_path / "output" / output_name).rename(tmp_path / output_name)

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_two_correct_output_names_with_swapped_document_bytes_are_rejected(
    tmp_path: Path,
) -> None:
    """验证两份正确新名下的源文档内容调包必须失败。

    输入参数：
        tmp_path：构造合法终态后交换前两份 DOCX 的完整字节。
    输出返回值：
        无；文件数、名称和格式都正确仍不足以通过，每份
        输出必须命中自己的源 size/SHA-256。
    """

    params = _prepare_valid_rename(tmp_path)
    first = tmp_path / "output" / params["filenames"][0]
    second = tmp_path / "output" / params["filenames"][1]
    first_payload = first.read_bytes()
    second_payload = second.read_bytes()
    first.write_bytes(second_payload)
    second.write_bytes(first_payload)

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_modified_naming_metadata_is_rejected_even_when_outputs_are_correct(
    tmp_path: Path,
) -> None:
    """验证完成重命名后篡改根级规则文件也会失败。

    输入参数：
        tmp_path：三份输出均正确，仅向 ``Naming_rules.txt``
            追加一段合成文本。
    输出返回值：
        无；被声明为原样保留的元数据 size/SHA 任一变化即零分。
    """

    params = _prepare_valid_rename(tmp_path)
    rules_path = tmp_path / "Naming_rules.txt"
    rules_path.write_text(
        rules_path.read_text(encoding="utf-8") + "\nmodified",
        encoding="utf-8",
    )

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_hash_matching_but_structurally_invalid_docx_is_rejected(
    tmp_path: Path,
) -> None:
    """验证摘要匹配也不能绕过 DOCX OPC 结构门。

    输入参数：
        tmp_path：把第一份输出替换为伪 OOXML ZIP，并同步合成
            合同的 size/SHA，以隔离格式检查行为。
    输出返回值：
        无；缺失 package relationship、正确 Content Type 与 Word
        主文档根的容器必须零分。
    """

    params = _prepare_valid_rename(tmp_path)
    document_spec = params["rename_contract"]["documents"][0]
    output_path = tmp_path / "output" / document_spec["output_filename"]
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"<root/>")
    size, sha256 = _identity(output_path)
    document_spec["source_size"] = size
    document_spec["source_sha256"] = sha256

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_revv_legacy_interpretation_is_rejected(tmp_path: Path) -> None:
    """验证把 Excel 前导 v 与 ``rev`` 直接拼成 ``revv`` 不能通过。

    输入参数：
        tmp_path：将第一份正确输出的 ``rev1.0`` 新名改为
            旧规则可能误解的 ``revv1.0``。
    输出返回值：
        无；文档字节未变仍必须因精确路径不匹配而零分。
    """

    params = _prepare_valid_rename(tmp_path)
    correct_name = params["filenames"][0]
    legacy_name = correct_name.replace("rev1.0", "revv1.0")
    (tmp_path / "output" / correct_name).rename(tmp_path / "output" / legacy_name)

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_unlisted_plain_file_is_rejected(tmp_path: Path) -> None:
    """验证非 Office 额外普通文件也不在终态闭集中。

    输入参数：
        tmp_path：已符合合同的合成终态，再注入一份根级普通文件。
    输出返回值：
        无；未声明普通文件必须使整个重命名合同零分。
    """

    params = _prepare_valid_rename(tmp_path)
    (tmp_path / "unlisted.log").write_text("extra", encoding="utf-8")

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_modified_project_workbook_is_rejected(tmp_path: Path) -> None:
    """验证项目主数据工作簿必须与固定输入逐字节一致。

    输入参数：
        tmp_path：输出完全正确，仅在 ``Project_Information.xlsx``
            容器尾部追加字节的合成树。
    输出返回值：
        无；工作簿的 size 或 SHA-256 任一漂移都必须零分。
    """

    params = _prepare_valid_rename(tmp_path)
    workbook = tmp_path / "Project_Information.xlsx"
    workbook.write_bytes(workbook.read_bytes() + b"modified")

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == 0.0


def test_duplicate_preserved_path_is_a_redacted_configuration_error(
    tmp_path: Path,
) -> None:
    """验证合同重复路径引发脱敏配置错误而非普通失配。

    输入参数：
        tmp_path：合法合成终态，但规则中重复一条保留文件规格。
    输出返回值：
        无；检查返回固定 ``evaluator_error``，不回显重复路径。
    """

    params = _prepare_valid_rename(tmp_path)
    preserved = params["rename_contract"]["preserved_files"]
    duplicated_path = preserved[0]["path"]
    preserved.append(deepcopy(preserved[0]))

    result = check_named_files_exist(os.fspath(tmp_path), params)

    assert result["pass"] is False
    assert result["score"] == -1.0
    assert result["status"] == "evaluator_error"
    assert duplicated_path not in repr(result)
