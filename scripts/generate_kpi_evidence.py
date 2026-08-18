#!/usr/bin/env python3
"""Generate an honest, machine-readable KPI evidence snapshot.

Synthetic contract evaluations are never promoted to operational KPI proof.
Metrics without a sufficient real sample stay ``insufficient_evidence``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services.agent_benchmark_eval import evaluate_agent_benchmarks  # noqa: E402
from app.services.execution_verification_eval import evaluate_declarations  # noqa: E402
from app.services.knowledge_rag_eval import evaluate_knowledge_rag  # noqa: E402
from app.services.run_telemetry import RunTelemetryService  # noqa: E402
from release_evidence import evaluate_release_evidence  # noqa: E402


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_checksum_manifest(output_dir: Path) -> dict[str, str]:
    """Hash evidence payloads without making the manifest self-referential."""
    return {
        path.name: _sha256(path)
        for path in sorted(output_dir.glob("*.json"))
        if path.name != "SHA256SUMS.json"
    }


def build_kpi_items(
    *,
    declaration: dict[str, Any],
    benchmark: dict[str, Any],
    rag: dict[str, Any],
    telemetry: dict[str, Any],
    release: dict[str, Any],
) -> list[dict[str, Any]]:
    declaration_summary = declaration["summary"]
    benchmark_summary = benchmark["summary"]
    recovery = telemetry.get("recovery") if telemetry.get("status") == "ready" else None
    release_summary = release["summary"]

    recovery_status = "insufficient_evidence"
    if recovery and recovery.get("sample_sufficient"):
        recovery_status = "pass" if recovery.get("meets_target") else "fail"

    return [
        {
            "id": "error_green_rate",
            "target": "错误绿色成功率 = 0",
            "status": (
                "pass"
                if declaration_summary.get("false_verified_error_green") == 0
                and declaration_summary.get("mismatches") == 0
                else "fail"
            ),
            "measurement": "100 条版本化执行声明离线评测",
            "value": {
                "total": declaration_summary.get("total"),
                "false_verified": declaration_summary.get("false_verified_error_green"),
                "mismatches": declaration_summary.get("mismatches"),
            },
        },
        {
            "id": "controlled_task_closure_rate",
            "target": "1.0 RC 受控任务闭环完成率 ≥ 80%",
            "status": "insufficient_evidence",
            "measurement": "确定性组件契约代理；不等同于真实 provider 端到端闭环",
            "value": {
                "proxy_total": benchmark_summary.get("total"),
                "proxy_matched": benchmark_summary.get("matched"),
                "proxy_categories": benchmark_summary.get("category_count"),
                "operational_rc_sample": 0,
            },
        },
        {
            "id": "first_task_p90_minutes",
            "target": "新用户首次闭环 p90 ≤ 5 分钟",
            "status": "insufficient_evidence",
            "measurement": "需要新用户计时样本；自动 E2E 只证明路径可用",
            "value": {"human_samples": 0, "p90_minutes": None},
        },
        {
            "id": "recovery_success_rate",
            "target": "可恢复 Run 恢复成功率 ≥ 95%",
            "status": recovery_status,
            "measurement": "真实本地 Run telemetry；至少 20 个已决恢复样本才判定",
            "value": recovery or {
                "sample_sufficient": False,
                "success_rate": None,
                "error_code": telemetry.get("error_code", "telemetry_unavailable"),
            },
        },
        {
            "id": "diagnostic_localization_rate",
            "target": "内测严重问题由诊断包定位率 ≥ 90%",
            "status": "insufficient_evidence",
            "measurement": "现有自动测试证明脱敏与 trace 完整性，尚无人工问题归因样本",
            "value": {"triaged_incidents": 0, "localized_incidents": 0},
        },
        {
            "id": "public_release_gate",
            "target": "签名、三平台、升级与供应链证据全部通过",
            "status": (
                "pass"
                if release_summary.get("public_1_0_go_no_go") == "Go"
                else "no_go"
            ),
            "measurement": "release_evidence 硬门禁",
            "value": {
                "local_dev_ready": release_summary.get("local_dev_ready"),
                "public_rc_ready": release_summary.get("public_rc_ready"),
                "decision": release_summary.get("public_1_0_go_no_go"),
                "public_missing": release_summary.get("public_rc_missing"),
            },
        },
        {
            "id": "knowledge_rag_quality",
            "target": "离线知识检索 Hit@3 ≥ 0.85、MRR ≥ 0.75",
            "status": "pass" if rag.get("passed") else "fail",
            "measurement": "固定离线 RAG 问答集（补充 KPI）",
            "value": rag.get("summary", {}),
        },
    ]


def _markdown(payload: dict[str, Any]) -> str:
    labels = {
        "pass": "通过",
        "fail": "失败",
        "no_go": "No-Go",
        "insufficient_evidence": "证据不足",
    }
    rows = []
    for item in payload["kpis"]:
        rows.append(
            f"| {item['target']} | {labels.get(item['status'], item['status'])} | "
            f"{item['measurement']} |"
        )
    return "\n".join([
        "# AetherChat P0/P1 KPI 证据快照",
        "",
        f"**生成时间（UTC）**：{payload['generated_at']}  ",
        f"**源码提交**：`{payload['source']['commit']}`  ",
        f"**工作区状态**：{'有未提交改动' if payload['source']['dirty'] else '干净'}",
        "",
        "| 指标 | 结论 | 证据边界 |",
        "|---|---|---|",
        *rows,
        "",
        "说明：确定性 benchmark、自动 E2E 与真实用户/真实 provider 运行样本严格分开；",
        "没有足够样本的指标保持“证据不足”，不会用代理测试冒充 KPI 达标。",
        "",
        "机器可读明细见同目录 `kpi-evidence.json` 及各评测 JSON。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home()
        / ".config"
        / "aetherchat-frontend"
        / "backend-data"
        / "aetherchat.sqlite3",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs" / "evidence" / "p0-p1-2026-08-13",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    declaration = evaluate_declarations().to_dict()
    benchmark = evaluate_agent_benchmarks().to_dict()
    rag = evaluate_knowledge_rag().to_dict()
    telemetry = RunTelemetryService(args.database).snapshot(run_limit=200)
    release = evaluate_release_evidence()

    components = {
        "execution-verification.json": declaration,
        "agent-benchmark.json": benchmark,
        "knowledge-rag.json": rag,
        "run-telemetry.json": telemetry,
        "release-evidence.json": release,
    }
    for name, value in components.items():
        (args.output_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    payload = {
        "schema": "aetherchat-kpi-evidence-v1",
        "generated_at": _now(),
        "source": {
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "kpis": build_kpi_items(
            declaration=declaration,
            benchmark=benchmark,
            rag=rag,
            telemetry=telemetry,
            release=release,
        ),
    }
    kpi_path = args.output_dir / "kpi-evidence.json"
    kpi_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")

    manifest = _build_checksum_manifest(args.output_dir)
    (args.output_dir / "SHA256SUMS.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for item in payload["kpis"]:
        print(f"{item['id']}: {item['status']}")
    print(f"wrote: {args.output_dir}")
    return 0 if all(item["status"] != "fail" for item in payload["kpis"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
