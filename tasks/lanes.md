# 文件职责与任务占用

修改前登记，完成后释放。当前任务串行执行；用户要求并行时，同一文件由一位执行者维护。约束见 [开发与验收规范](../docs/协作规范.md)。

| 范围 | 主要文件 | 当前任务 |
|---|---|---|
| 学生端 | `csp_exam/web/student_pages.py`、`ui.py`、`static/**` | 空闲 |
| 管理端 | `csp_exam/web/admin_pages.py`、`scoreboard.py`、`judge_settings.py` | 空闲 |
| 业务核心 | `csp_exam/core/**`、`csp_exam/config.py` | 空闲 |
| 验证与工具 | `csp_exam/tests/**`、`csp_exam/tools/**`、根目录 `tests/**` | 空闲 |
| 文档 | `docs/**`、`tasks/**`、各级 README 与索引 | 空闲 |

`web/server.py`、`config.py` 和数据结构属于共享范围，变更由当前任务统一核对调用者和验证；避免不同任务同时写入。
