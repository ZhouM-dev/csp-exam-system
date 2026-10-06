# CSP 模拟考试系统

教师创建比赛、配置题目与名单；学生使用考号登录，上传考号文件夹交卷。CSP / OI 取最后一次交卷，IOI 按题保留最高分。三种赛制均由教师公布成绩后向学生提供分数。

当前架构是 Python 标准库 HTTP 服务、本地 JSON/题库和 go-judge 沙箱。判题不依赖 Hydro 或 DOMjudge 实例；排行榜采用 DOMjudge 风格，显示各题分数及总分，与管理端共用数据和公开开关。

- [使用说明](csp_exam/README.md)
- [需求与规则](docs/设计说明.md)
- [架构与职责](docs/架构.md)
- [运维手册](docs/运维手册.md)
- [题单导入接口](csp_exam/题目单导入接口.md)
- [CSP 环境审计](docs/CSP评测审计-2026-10-06.md)
- [历史重测记录](docs/历史重测-2026-10-07.md)
- [收尾检查记录](docs/项目收尾-2026-10-07.md)

开发入口是 `run.py`，源码在 `csp_exam/`。`data/` 保存业务资料，`logs/` 保存运行日志。本地不包含生产学生资料。需要独立部署 go-judge 与经核验的 NOI Linux 编译环境；具体配置见运维手册。

```bash
python3 run.py
bash tests/_regress_all.sh --quick
# 在配置好实际沙箱的服务器运行完整验收：
bash tests/_regress_all.sh --live
```

云服务器的 CPU 与官方评测硬件不同。软件环境已经对齐已公布的 CSP-J/S 2025 与 NOI Linux 2.0 要求；时间倍率默认 1.0，尚未用官方同款主机完成性能校准。

## 版本控制

使用 Git 保存源码、文档与有效回归测试；运行数据、成绩导出、日志、凭据与备份由 `.gitignore` 排除。审计文档仅保存汇总，逐人成绩与原始资料保留在私有恢复档案。

当前工作分支为 `dev`，首次推送使用 `git push -u origin dev`，后续提交后使用 `git push`。禁止强制覆盖共享分支；推送前检查暂存内容和 `git diff --check`。部署按前述脚本执行，业务数据不进入仓库。
