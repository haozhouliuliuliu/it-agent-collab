"""评估集自检。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CASES = json.loads((Path(__file__).resolve().parents[1] / "eval" / "cases.json").read_text(encoding="utf-8"))


def test_eight_cases() -> None:
    assert len(CASES) == 8


def test_six_multi_agent_cases() -> None:
    counts = Counter(case["category"] for case in CASES)
    assert counts == {"simple": 2, "multi": 6}


def test_ids_unique_and_expectations_present() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["message"]
        assert case["expect"]["status_in"]
        assert case["expect"]["min_models"] >= 2


def test_multi_cases_require_auditor_or_conflict() -> None:
    for case in CASES:
        if case["category"] != "multi":
            continue
        expect = case["expect"]
        three_agents = "auditor" in expect.get("required_agents", []) or "policy" in expect.get("required_agents", [])
        assert three_agents or "policy_subtask_status_in" in expect
