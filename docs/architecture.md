# 架构与数据流

## 1. 组件

```text
CLI (team-agent)
  └─ Orchestrator
       ├─ PlannerAgent      deepseek-flash
       ├─ BusinessAgent     qwen3.8-flash + 5 只读工具
       ├─ PolicyAgent       qwen3.8-flash + 制度检索
       ├─ AuditorAgent      kimi-k2.7-code（无工具）
       ├─ LLMClient         每供应商限流、超时、用量、错误码
       ├─ CallBudget        全局 50 / 单任务 12
       └─ Repository        SQLite：任务/子任务/调用/交接/审核/事件
```

## 2. 执行时序

1. `create_task`：写入 `collab_tasks(status=planning)` 与 `task_created` 事件；
2. Planner：一次模型调用，输出结构化 `Plan`；程序补齐 business、去重、policy 存在则强制审核；
3. 创建子任务（business/policy/auditor），状态 `pending`；
4. 若 `plan.parallel`，用 `ThreadPoolExecutor(2)` 并行执行 business 与 policy；
5. 每个调查 Agent：
   - 第一次模型调用：返回 tool_calls；
   - 程序执行工具（最多 3 次），生成 `EV-` 证据，tool 结果回灌；
   - 第二次模型调用：输出结构化结论；
   - 每次调用写 `collab_runs`，子任务状态与输出写 `collab_subtasks`；
6. 交接：business/policy 输出写 `collab_handoffs`，目标是 auditor；
7. Auditor：一次模型调用，输入包含计划、双方结论、证据目录（ID + 摘要）、程序发现的问题；
8. 程序校验审核引用的证据 ID；伪造证据、未支持结论、不可信指令会追加 finding 并降级 verdict；
9. 受控返工：`needs_evidence` 且有 followup 时，最多一轮，定向补查 business 或 policy，再次审核；
10. 汇总：`completed / partial / failed`，写 `final_json` 与 `task_finished` 事件。

## 3. 状态机

任务：

```text
planning → running → completed
                   ├→ partial（审核未通过/子任务失败/证据不足）
                   └→ failed（规划或业务调查失败）
```

子任务：`pending → running → completed | failed | skipped`。

审核：`pass | needs_evidence | fail`。

## 4. 证据模型

- 工具入参经 Pydantic 校验后执行；结果哈希生成 `EV-xxxxxxxx`；
- 证据同时写入工具返回与最终报告 `evidence[]`；
- Agent 的 `claims[].evidence_ids` 必须引用真实存在的 ID；
- 审核 Agent 看到的是证据目录（ID、工具、摘录），不是任意文本；
- 程序检测到伪造 ID 时添加 `invalid_evidence` 错误并降级。

## 5. 预算与限流

- `CallBudget(max_total=50, max_per_task=12)`：每次模型请求（含重试与失败）计数；
- 单次 prompt 字符数上限 30000，输出 token 上限 6000；
- 供应商限流：Kimi 默认 21 秒/请求（账户 3 RPM），Qwen/DeepSeek 不限；
- 超时 180 秒；可重试错误最多 2 次尝试；401/404/400 不重试；
- 预算耗尽抛 `BudgetExceeded`，子任务失败、任务降级，不伪造成功。

## 6. 持久化

| 表 | 内容 |
|---|---|
| `collab_tasks` | 请求、状态、计划、最终报告、调用数、token |
| `collab_subtasks` | Agent、目标、状态、结构化输出、错误 |
| `collab_runs` | 每次模型调用：模型、阶段、耗时、token、错误 |
| `collab_handoffs` | 上游 Agent → 下游 Agent 的结构化载荷 |
| `collab_audits` | 每次审核的 verdict 与 findings |
| `collab_events` | 计划、子任务、审核、返工、完成等事件 |

SQLite 使用 WAL，重启后历史任务与轨迹仍可读取；新任务不会覆盖旧记录。

## 7. 安全边界

- 工具注册表只有 5 个只读工具；
- 无写工具、无 SQL、无命令、无文件访问；
- 文档中的指令只作为数据，程序会正则标记，审核 Agent 会复核；
- 模型失败写错误码，任务状态降级；不会用固定答案填充。
