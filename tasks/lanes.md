# 车道占用表

**规则：同一时间，一个文件只属于一条车道。** 开工前在这里登记，收尾时清空。
车道定义与文件白名单见《[协作规范](../docs/协作规范.md)》第二节。

| 车道 | 负责范围 | 当前占用（任务编号 / 对话） |
|---|---|---|
| A 学生端 | `web/student_pages.py`、`web/ui.py`、`web/static/**` | 空闲 |
| B 管理端 | `web/admin_pages.py`、`web/urls.py` | 空闲 |
| C 业务核心 | `core/**`、`config.py` | 空闲 |
| D 验证与工具 | `build/**`、`csp_exam/tests/**`、`csp_exam/tools/**`、服务器 `tests/**` | 空闲 |
| E 文档与外观 | `docs/**`、`csp_exam/README.md`、`csp-ui/**`、根 `README.md` | 空闲 |

## 共享热点（同一时间只允许一条车道动）

| 文件 | 约定 |
|---|---|
| `csp_exam/web/server.py` | 新增路由时只加 1–2 行，别顺手重构 |
| `csp_exam/config.py` | 只增常量，不改名不删 |
| `docs/设计说明.md` | 各车道只补自己那部分；冲突由主对话合并 |

## 登记格式

```
| A 学生端 | … | T-0003（对话：学生端深色模式）|
```

完成后把该格改回 `空闲`，并在任务文件末尾写摘要。
