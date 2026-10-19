"""协作任务的持久化：主任务、子任务、模型调用、交接、审核与事件。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .db import connect
from .domain import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS collab_tasks (
    id           TEXT PRIMARY KEY,
    user_input   TEXT NOT NULL,
    status       TEXT NOT NULL,
    plan_json    TEXT,
    final_json   TEXT,
    model_calls  INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collab_subtasks (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES collab_tasks(id) ON DELETE CASCADE,
    agent         TEXT NOT NULL,
    objective     TEXT NOT NULL,
    status        TEXT NOT NULL,
    output_json   TEXT,
    error         TEXT,
    parallel_group TEXT,
    started_at    TEXT,
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS collab_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL REFERENCES collab_tasks(id) ON DELETE CASCADE,
    subtask_id       TEXT,
    agent            TEXT NOT NULL,
    model            TEXT NOT NULL,
    phase            TEXT NOT NULL,
    status           TEXT NOT NULL,
    prompt_chars     INTEGER NOT NULL,
    output_json      TEXT,
    error_code       TEXT,
    error_message    TEXT,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collab_handoffs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES collab_tasks(id) ON DELETE CASCADE,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collab_audits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES collab_tasks(id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL,
    output_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collab_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES collab_tasks(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_collab_tasks_status ON collab_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_collab_runs_task ON collab_runs(task_id, id);
"""


class TaskNotFound(Exception):
    pass


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.db_path)
        connection.executescript(SCHEMA)
        return connection

    def create_task(self, user_input: str) -> str:
        task_id = f"T5-{uuid.uuid4().hex[:8]}"
        at = now_iso()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO collab_tasks (id, user_input, status, created_at, updated_at) "
                "VALUES (?, ?, 'planning', ?, ?)",
                (task_id, user_input, at, at),
            )
            connection.commit()
        finally:
            connection.close()
        self.add_event(task_id, "task_created", {"input": user_input})
        return task_id

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"status", "plan_json", "final_json", "model_calls", "total_tokens", "error"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新字段：{unknown}")
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        connection = self._connect()
        try:
            connection.execute(
                f"UPDATE collab_tasks SET {assignments} WHERE id = ?",
                (*fields.values(), task_id),
            )
            connection.commit()
        finally:
            connection.close()

    def get_task(self, task_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM collab_tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise TaskNotFound(f"任务不存在：{task_id}")
        return dict(row)

    def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM collab_tasks ORDER BY created_at DESC, id LIMIT ?", (limit,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def create_subtask(self, task_id: str, agent: str, objective: str, parallel_group: str | None) -> str:
        subtask_id = f"ST-{uuid.uuid4().hex[:8]}"
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO collab_subtasks (id, task_id, agent, objective, status, parallel_group) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (subtask_id, task_id, agent, objective, parallel_group),
            )
            connection.commit()
        finally:
            connection.close()
        return subtask_id

    def update_subtask(self, subtask_id: str, **fields: Any) -> None:
        allowed = {"status", "output_json", "error", "started_at", "finished_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新字段：{unknown}")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        connection = self._connect()
        try:
            connection.execute(
                f"UPDATE collab_subtasks SET {assignments} WHERE id = ?",
                (*fields.values(), subtask_id),
            )
            connection.commit()
        finally:
            connection.close()

    def list_subtasks(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM collab_subtasks WHERE task_id = ? ORDER BY rowid", (task_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def log_run(
        self,
        *,
        task_id: str,
        agent: str,
        model: str,
        phase: str,
        status: str,
        prompt_chars: int,
        subtask_id: str | None = None,
        output: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO collab_runs (task_id, subtask_id, agent, model, phase, status, prompt_chars, "
                "output_json, error_code, error_message, prompt_tokens, completion_tokens, latency_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    subtask_id,
                    agent,
                    model,
                    phase,
                    status,
                    prompt_chars,
                    json.dumps(output, ensure_ascii=False) if output is not None else None,
                    error_code,
                    error_message,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    now_iso(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def list_runs(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM collab_runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def add_handoff(self, task_id: str, from_agent: str, to_agent: str, payload: Any) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO collab_handoffs (task_id, from_agent, to_agent, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, from_agent, to_agent, json.dumps(payload, ensure_ascii=False), now_iso()),
            )
            connection.commit()
        finally:
            connection.close()

    def list_handoffs(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM collab_handoffs WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def add_audit(self, task_id: str, verdict: str, output: Any) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO collab_audits (task_id, verdict, output_json, created_at) VALUES (?, ?, ?, ?)",
                (task_id, verdict, json.dumps(output, ensure_ascii=False), now_iso()),
            )
            connection.commit()
        finally:
            connection.close()

    def list_audits(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM collab_audits WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        connection = self._connect()
        try:
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM collab_events WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO collab_events (task_id, seq, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, seq, event_type, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
            )
            connection.commit()
        finally:
            connection.close()

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM collab_events WHERE task_id = ? ORDER BY seq", (task_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def add_usage(self, task_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE collab_tasks SET model_calls = model_calls + 1, "
                "total_tokens = total_tokens + ?, updated_at = ? WHERE id = ?",
                (prompt_tokens + completion_tokens, now_iso(), task_id),
            )
            connection.commit()
        finally:
            connection.close()
