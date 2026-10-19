"""真实模型端到端评估：8 个用例，全局模型调用硬上限 50 次。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from team_agent.config import Settings
from team_agent.domain import now_iso
from team_agent.llm import CallBudget
from team_agent.orchestrator import build_system

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "eval" / "cases.json"
EVIDENCE_ROOT = ROOT / "evidence" / "real-model"


def _judge(case: dict[str, Any], detail: dict[str, Any], audit: dict[str, Any] | None) -> list[str]:
    expect = case["expect"]
    failures: list[str] = []
    task = detail["task"]
    final = json.loads(task["final_json"]) if task["final_json"] else {}
    subtasks = detail["subtasks"]
    agents = {item["agent"] for item in subtasks}

    if task["status"] not in expect.get("status_in", ["completed"]):
        failures.append(f"状态期望 {expect['status_in']}，实际 {task['status']}")

    for agent in expect.get("required_agents", []):
        if agent not in agents:
            failures.append(f"缺少 Agent：{agent}")
    for agent in expect.get("forbidden_agents", []):
        if agent in agents:
            failures.append(f"不应出现 Agent：{agent}")

    if "policy_subtask_status_in" in expect:
        policy_rows = [item for item in subtasks if item["agent"] == "policy"]
        status = policy_rows[0]["status"] if policy_rows else "missing"
        if status not in expect["policy_subtask_status_in"]:
            failures.append(f"制度子任务状态期望 {expect['policy_subtask_status_in']}，实际 {status}")

    if "audit_verdict_in" in expect:
        verdict = audit["verdict"] if audit else None
        if verdict not in expect["audit_verdict_in"]:
            failures.append(f"审核结论期望 {expect['audit_verdict_in']}，实际 {verdict}")

    text = json.dumps(final, ensure_ascii=False)
    for needle in expect.get("final_contains_all", []):
        if needle not in text:
            failures.append(f"最终报告缺少：{needle}")
    any_of = expect.get("final_contains_any", [])
    if any_of and not any(needle in text for needle in any_of):
        failures.append(f"最终报告不包含任一关键词：{any_of}")
    for needle in expect.get("final_not_contains", []):
        if needle in text:
            failures.append(f"最终报告不应包含：{needle}")

    if "policy_must_mention" in expect:
        policy_text = json.dumps(final.get("policy_summary"), ensure_ascii=False)
        policy_text += json.dumps(detail.get("subtasks"), ensure_ascii=False)
        for needle in expect["policy_must_mention"]:
            if needle not in policy_text:
                failures.append(f"制度结论未提及：{needle}")

    if "audit_findings_any" in expect:
        findings = audit.get("findings", []) if audit else []
        corpus = json.dumps(findings, ensure_ascii=False)
        if not any(needle in corpus for needle in expect["audit_findings_any"]):
            failures.append(f"审核未给出相关发现：{expect['audit_findings_any']}")

    runs = detail["runs"]
    models = {run["model"] for run in runs if run["status"] == "ok"}
    if len(models) < int(expect.get("min_models", 2)):
        failures.append(f"实际参与模型不足：{sorted(models)}")
    if expect.get("auditor_differs_from_business"):
        business_models = {run["model"] for run in runs if run["agent"] == "business"}
        auditor_models = {run["model"] for run in runs if run["agent"] == "auditor"}
        if business_models & auditor_models:
            failures.append("审核模型与业务调查模型相同")

    return failures


def _run_case(case: dict[str, Any], budget: CallBudget, workdir: Path) -> dict[str, Any]:
    settings = Settings.from_env()
    db_path = workdir / f"{case['id']}.db"
    if "policy_model_override" in case:
        assert settings.policy is not None
        settings = replace(
            settings, policy=replace(settings.policy, model=case["policy_model_override"]), db_path=db_path
        )
    else:
        settings = replace(settings, db_path=db_path)

    system = build_system(settings)
    system.budget = budget
    started = time.time()
    before_calls = budget.total_calls
    error = None
    try:
        task_id = system.run(case["message"])
    except Exception as exc:  # 单用例异常不中断整轮评估
        error = f"{exc.__class__.__name__}: {exc}"
        task_id = None
    duration = round(time.time() - started, 1)

    if task_id is None:
        return {
            "id": case["id"],
            "title": case["title"],
            "category": case["category"],
            "passed": False,
            "failures": [f"执行异常：{error}"],
            "duration_s": duration,
            "calls": budget.total_calls - before_calls,
            "detail": {},
        }

    detail = {
        "task": system.repository.get_task(task_id),
        "subtasks": system.repository.list_subtasks(task_id),
        "runs": system.repository.list_runs(task_id),
        "handoffs": system.repository.list_handoffs(task_id),
        "audits": system.repository.list_audits(task_id),
        "events": system.repository.list_events(task_id),
    }
    audit = json.loads(detail["audits"][-1]["output_json"]) if detail["audits"] else None
    failures = _judge(case, detail, audit)
    return {
        "id": case["id"],
        "title": case["title"],
        "category": case["category"],
        "passed": not failures,
        "failures": failures,
        "duration_s": duration,
        "calls": budget.total_calls - before_calls,
        "models": sorted({run["model"] for run in detail["runs"] if run["status"] == "ok"}),
        "status": detail["task"]["status"],
        "audit_verdict": audit["verdict"] if audit else None,
        "tokens": detail["task"]["total_tokens"],
        "detail": detail,
        "error": error,
    }


def _write_report(run_dir: Path, config: dict[str, Any], results: list[dict[str, Any]]) -> None:
    passed = sum(1 for item in results if item["passed"])
    multi = [item for item in results if item["category"] == "multi"]
    multi_passed = sum(1 for item in multi if item["passed"])
    lines = [
        "# 真实模型多 Agent 评估报告",
        "",
        f"- 运行：`{config['run_id']}`，模型调用 {config['calls_used']} 次（硬上限 {config['max_total_calls']}）",
        f"- 规划：`{config['models']['planner']}`；业务：`{config['models']['business']}`；"
        f"制度：`{config['models']['policy']}`；审核：`{config['models']['auditor']}`",
        f"- 用例：{len(results)}，通过 {passed}；多 Agent 用例 {multi_passed}/{len(multi)}",
        "",
        "| 用例 | 状态 | 审核 | 模型数 | 调用 | 结果 | 失败原因 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        mark = "通过" if item["passed"] else "失败"
        reasons = "；".join(item["failures"]) if item["failures"] else "-"
        lines.append(
            f"| {item['id']} | {item.get('status', '-')} | {item.get('audit_verdict', '-')} | "
            f"{len(item.get('models', []))} | {item['calls']} | {mark} | {reasons} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- 全部用例使用真实模型，调用链见同目录 `cases.json`；",
        "- 失败与冲突原样保留，不做删除或重跑美化。",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args(argv)

    if (
        not os.environ.get("DEEPSEEK_API_KEY")
        or not os.environ.get("QWEN_API_KEY")
        or not os.environ.get("KIMI_API_KEY")
    ):
        print("缺少模型密钥环境变量")
        return 3

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    run_id = datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    run_dir = args.output / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="it-agent-collab-eval-"))

    settings = Settings.from_env()
    budget = CallBudget(settings.max_total_calls, settings.max_calls_per_task)
    config = {
        "run_id": run_id,
        "started_at": now_iso(),
        "max_total_calls": settings.max_total_calls,
        "max_calls_per_task": settings.max_calls_per_task,
        "max_prompt_chars": settings.max_prompt_chars,
        "max_completion_tokens": settings.max_completion_tokens,
        "revision_limit": settings.revision_limit,
        "models": {
            "planner": settings.planner.model,
            "business": settings.business.model,
            "policy": settings.policy.model,
            "auditor": settings.auditor.model,
        },
        "providers": {
            "planner": settings.planner.api_base,
            "business": settings.business.api_base,
            "policy": settings.policy.api_base,
            "auditor": settings.auditor.api_base,
        },
    }

    results: list[dict[str, Any]] = []
    for case in cases:
        if budget.total_calls >= budget.max_total:
            results.append(
                {
                    "id": case["id"],
                    "title": case["title"],
                    "category": case["category"],
                    "passed": False,
                    "failures": ["全局调用预算耗尽，未执行"],
                    "duration_s": 0,
                    "calls": 0,
                    "models": [],
                    "detail": {},
                }
            )
            continue
        result = _run_case(case, budget, workdir)
        results.append(result)
        print(
            f"{result['id']:<3} {'PASS' if result['passed'] else 'FAIL'} "
            f"{result.get('status', '-'):<9} calls={result['calls']} {result['failures']}",
            flush=True,
        )

    config["calls_used"] = budget.total_calls
    config["finished_at"] = now_iso()
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "cases.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(run_dir, config, results)

    passed = sum(1 for item in results if item["passed"])
    multi = [item for item in results if item["category"] == "multi"]
    multi_passed = sum(1 for item in multi if item["passed"])
    print(f"\n通过 {passed}/{len(results)}；多 Agent {multi_passed}/{len(multi)}；调用 {budget.total_calls} 次")
    print(f"证据：{run_dir}")
    return 0 if passed >= 7 and multi_passed >= 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
