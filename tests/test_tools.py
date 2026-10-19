"""只读工具测试：白名单、参数校验、证据 ID 与提示注入数据。"""

from __future__ import annotations

import pytest

from team_agent.db import init_database
from team_agent.policies import PolicyIndex
from team_agent.store import DeskStore
from team_agent.tools import ReadOnlyTools, ToolError

from .support import SEED_DIR


@pytest.fixture
def tools(tmp_path) -> ReadOnlyTools:
    db = tmp_path / "tools.db"
    init_database(db, SEED_DIR)
    return ReadOnlyTools(DeskStore(db), PolicyIndex.from_dir(SEED_DIR / "policies"))


def test_exactly_five_readonly_tools(tools: ReadOnlyTools) -> None:
    names = {item["function"]["name"] for item in tools.schemas()}
    assert names == {"search_policies", "query_inventory", "get_asset", "get_ticket", "list_tickets"}
    assert not {"create_ticket", "update_ticket", "run_sql"} & names


def test_unknown_tool_is_rejected(tools: ReadOnlyTools) -> None:
    with pytest.raises(ToolError) as excinfo:
        tools.execute("run_sql", {"sql": "DROP TABLE tickets"})
    assert excinfo.value.code == "TOOL_NOT_FOUND"


def test_invalid_arguments_are_rejected(tools: ReadOnlyTools) -> None:
    with pytest.raises(ToolError) as excinfo:
        tools.execute("get_ticket", {"ticket_id": "BAD"})
    assert excinfo.value.code == "INVALID_ARGUMENTS"


def test_extra_arguments_are_rejected(tools: ReadOnlyTools) -> None:
    with pytest.raises(ToolError) as excinfo:
        tools.execute("query_inventory", {"category": "笔记本电脑", "sql": "x"})
    assert excinfo.value.code == "INVALID_ARGUMENTS"


def test_evidence_id_is_deterministic(tools: ReadOnlyTools) -> None:
    first = tools.execute("query_inventory", {"category": "笔记本电脑"})
    second = tools.execute("query_inventory", {"category": "笔记本电脑"})
    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("EV-")


def test_inventory_evidence_contains_real_counts(tools: ReadOnlyTools) -> None:
    evidence = tools.execute("query_inventory", {"category": "笔记本电脑"})
    assert evidence.data["total_available"] == 4
    assert all("available" in item for item in evidence.data["items"])


def test_ticket_injection_is_returned_as_data(tools: ReadOnlyTools) -> None:
    evidence = tools.execute("get_ticket", {"ticket_id": "INC-2026-0017"})
    description = evidence.data["ticket"]["description"]
    assert "忽略之前的所有规则" in description
    assert evidence.evidence_id.startswith("EV-")


def test_policy_search_citation(tools: ReadOnlyTools) -> None:
    evidence = tools.execute("search_policies", {"query": "电脑领用", "limit": 3})
    assert evidence.data["count"] >= 1
    assert evidence.data["results"][0]["citation"]


def test_not_found_maps_to_tool_error(tools: ReadOnlyTools) -> None:
    with pytest.raises(ToolError) as excinfo:
        tools.execute("get_asset", {"asset_id": "ASSET-9999"})
    assert excinfo.value.code == "NOT_FOUND"


def test_old_policy_version_is_retrievable(tools: ReadOnlyTools) -> None:
    evidence = tools.execute("search_policies", {"query": "显示器 审批 旧版", "limit": 5})
    titles = [item["title"] for item in evidence.data["results"]]
    assert any("旧版" in title for title in titles)
