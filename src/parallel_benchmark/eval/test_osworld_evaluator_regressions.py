import json
import logging
from pathlib import Path

from parallel_benchmark.eval import osworld_evaluator as osw


LOG = logging.getLogger("test_osworld_evaluator_regressions")


def test_multi_vm_file_downloads_sidecar(monkeypatch, tmp_path):
    def fake_download(_vm_ip, _host_path, local_path, _log):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
        return True

    monkeypatch.setattr(osw, "_ssh_download_file", fake_download)

    cfg = {
        "type": "vm_file",
        "path": [
            "/home/user/Desktop/GRF-p5y.xlsx",
            "/home/user/Desktop/GRF-p5y-Sheet1.csv",
        ],
        "dest": ["GRF-p5y.xlsx", "GRF-p5y-Sheet1.csv"],
        "multi": True,
    }

    result = osw._get_result_file(cfg, "127.0.0.1", "/host/shared", str(tmp_path), LOG)

    assert isinstance(result, str)
    assert Path(result).name == "GRF-p5y.xlsx"
    assert Path(result).with_name("GRF-p5y-Sheet1.csv").read_text(encoding="utf-8") == (
        "GRF-p5y-Sheet1.csv"
    )


def test_multi_metric_conjunction_rejects_extra_invoice(monkeypatch, tmp_path):
    evaluator_json = tmp_path / "evaluator.json"
    evaluator_json.write_text(
        json.dumps(
            {
                "evaluator": {
                    "func": ["compare_pdfs", "check_include_exclude"],
                    "result": [
                        {"type": "vm_file", "path": "/problematic/Invoice # 243729.pdf"},
                        {"type": "vm_command_line", "command": "ls"},
                    ],
                    "expected": [
                        {"type": "cloud_file", "path": "https://example.test/gold.pdf"},
                        {
                            "type": "rule",
                            "rules": {
                                "include": ["Invoice # 243729.pdf"],
                                "exclude": ["invoice TII-20220301-90.pdf"],
                            },
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_get_result(cfg, *_args):
        if cfg["type"] == "vm_file":
            return str(tmp_path / "gold.pdf"), "vm_file"
        if cfg["type"] == "vm_command_line":
            return "Invoice # 243729.pdf\ninvoice TII-20220301-90.pdf\n", "vm_command_line"
        return None, cfg.get("type")

    def fake_get_expected(cfg, *_args):
        if cfg["type"] == "cloud_file":
            return str(tmp_path / "gold.pdf"), "cloud_file"
        if cfg["type"] == "rule":
            return cfg["rules"], "rule"
        return None, cfg.get("type")

    monkeypatch.setattr(osw, "_run_postconfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(osw, "_get_result", fake_get_result)
    monkeypatch.setattr(osw, "_get_expected", fake_get_expected)
    monkeypatch.setattr(osw, "_persist_result_data", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        osw,
        "_cached_eval_funcs",
        {
            "compare_pdfs": lambda _result, _expected: 1.0,
            "check_include_exclude": (
                lambda result, rules: float(
                    all(x in result for x in rules["include"])
                    and all(x not in result for x in rules["exclude"])
                )
            ),
        },
    )

    result = osw.evaluate_osworld_task(
        str(evaluator_json), "127.0.0.1", 5000, "/host/shared", LOG, "",
    )

    assert result["status"] == "ok"
    assert result["score"] == 0.0
    assert result["pass"] is False
