# 真实模型多 Agent 评估报告

- 运行：`run-20261019-091621`，模型调用 45 次（硬上限 50）
- 规划：`deepseek-flash`；业务：`qwen3.8-flash`；制度：`qwen3.8-flash`；审核：`kimi-k2.7-code`
- 用例：8，通过 8；多 Agent 用例 6/6

| 用例 | 状态 | 审核 | 模型数 | 调用 | 结果 | 失败原因 |
|---|---|---|---|---|---|---|
| S1 | completed | None | 2 | 3 | 通过 | - |
| S2 | completed | None | 2 | 3 | 通过 | - |
| M1 | partial | needs_evidence | 3 | 6 | 通过 | - |
| M2 | partial | needs_evidence | 3 | 7 | 通过 | - |
| M3 | partial | needs_evidence | 3 | 7 | 通过 | - |
| M4 | partial | needs_evidence | 3 | 8 | 通过 | - |
| M5 | partial | needs_evidence | 3 | 7 | 通过 | - |
| M6 | partial | None | 2 | 4 | 通过 | - |

## 说明

- 全部用例使用真实模型，调用链见同目录 `cases.json`；
- 失败与冲突原样保留，不做删除或重跑美化。