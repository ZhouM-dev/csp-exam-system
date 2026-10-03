# 判题后端：从 Hydro 换成 go-judge

> **状态：已切换完成**（本轮改的）。判题全部在本地 go-judge 上跑，Hydro 已停
> （容器 `stop`、守卫服务 `disable`，数据目录原样留着，`docker compose start` 就能回退）。
> 本文记的是**怎么换的、换到哪一步、还差什么**；功能性的说明见《设计说明》。

## 为什么换

原来 Hydro 一个人干三件事：存题+存数据、编译运行（沙箱）、比对。换成
[go-judge](https://github.com/criyle/go-judge) 之后它只干**沙箱**这一件：
「编译这段代码」「按这些限额跑这个程序」，题目数据与判分口径都回到考试服务自己手里。

最直接的收益是**判分口径自己说了算**，尤其是文件输入输出（freopen）：

* 旧做法：给学生的代码**包一层 freopen 垫片**（`core/wrapper.py`），因为评测机只喂 stdin。
  包与不包的结果不一样，"有没有 freopen 差异"就是这么来的。
* 新做法：学生的代码**一个字都不动**，跑完之后看它到底把答案写进了 `<英文名>.out`
  还是打印到了标准输出，按 `io_mode` 决定算哪个（见下表）。

| 学生写法 | `auto`（默认） | `file`（严格，真实 CSP 口径） | `stdin`（OI/IOI 口径） |
|---|---|---|---|
| 只写标准输入输出 | 满分 | **0 分**，并注明"没有把答案写进 xxx.out" | 满分 |
| 自己 `freopen` | 满分 | 满分 | 0 分（读不到输入文件） |

三种赛制的默认：CSP → `auto`，OI/IOI → `stdin`。`auto` 与旧的垫片行为一致，
所以**老提交重测不会因此大变**；想按真实考场判（漏 freopen 就得 0）就切 `file`。

## 已经做完的

1. **go-judge 装好并常驻**：`systemd` 服务 `go-judge`，
   `/usr/local/bin/go-judge -http-addr 127.0.0.1:5050 -mount-conf /etc/go-judge/mount.yaml
   -dir /var/lib/go-judge/files -no-fallback`。
   * 二进制来自官方镜像 `criyle/go-judge:latest`（GitHub 资产的下载会被掐断，
     镜像里的 `/opt/go-judge` 与 release 的 sha256 一模一样：`700d0ba6…`）。
   * 只监听本机（它没有鉴权）；沙箱里**看不到** `/root/csp-exam` 下的任何东西（已验）。
   * cgroup v2 + seccomp 生效（`-no-fallback`：拿不到就退出，不悄悄降级）。
2. **`core/gojudge.py`**：客户端（`probe()` 体检、`run()` 跑命令、`drop()` 清缓存）。
   实测过的字段名：`cpuLimit`/`clockLimit`（纳秒）、`memoryLimit`（字节）、
   `copyIn`（`{"content": 原文}` 或 `{"fileId": …}`）、`copyOutCached`；
   `/run` 的响应**就是结果数组本身**；状态串 `Accepted / Time Limit Exceeded /
   Memory Limit Exceeded / Nonzero Exit Status / Signalled / …`。
   `selftest()` 真跑死循环与吃内存，确认限额拦得住。
3. **`core/judgelocal.py`**：本地判题引擎。
   编译一次（产物留在沙箱仓库里给每个点引用）→ 逐点跑 → 按 CSP 口径比对
   （逐行去行末空白、去文尾空行后全文比较）→ 产出**与旧 Hydro 记录同形状**的 row
   （`status`/`testcases[].score`/`time`(毫秒)/`memory`(KB)/`compilerTexts`），
   所以上游 `grading._apply_result` 与两个端的页面都不用改。
   `selftest()` 覆盖上面那张表（9 条）。
   * 单点跑法：`sh -c 'rm -f <名>.out; ./main < <名>.in; ec=$?; 有 .out 就收进
     __answer.txt 并记 FILE，否则记 STDOUT; exit $ec'` —— 一次 `/run` 同时拿到
     「程序写的文件」「标准输出」「走的哪条路」，不用猜、也不用改学生代码。
   * `copyOut` **不能**列可能不存在的文件（整次运行会变成 `File Error`），
     所以上面用 `__answer.txt`/`__used.txt` 两个固定文件兜住。

## 已经做完的（续）

4. **题目数据落到本地**：`tests/_migrate_from_hydro.py`（**只读 Hydro、只写本地**）——
   30 道题里 29 道迁完（408MB，点数 2~20 全带答案），只有那道内部测试题 `#5` 本来就没数据。
   题面同时落了正本到 `data/statements/<pid>.md`。
5. **判分入口接上**：`grading.judge_csp()` 与 `judge_code()` 都改成
   `judgelocal.judge_source()` / `localoj.judge()`；`_apply_result` 之后的链路一行没动
   （所以成绩表、逐点明细、学生端显示都不用改）。
6. **其余 Hydro 依赖全部换掉**：
   * 题面读写 → `data/statements/<pid>.md` 就是正本（`localoj.problem_statement/set_problem_statement`，
     改题面页**不用再两处都写**了）；
   * 逐点明细的「看输入/标准答案」→ 读本地数据文件（`localoj.read_problem_case`）；
   * 题库列表 / 配题候选 / 建题查重 → `localoj.list_problems()` / `problem_pids()`（本地题目库）；
   * 建题 → `judgelocal.store_problem()`（**清空后重写**，重建同一道题不会残留旧测试点）；
   * 彻底删除 → `judgelocal.drop_problem_data()`（删本地测试点 + 题面）；
   * 「自己测试」→ 本地判一遍（编译报错原文照旧给老师看）；
   * 学生评测站账号（`ensure_account` / 「建账号」按钮）→ **不需要了**，函数保留但不做事。
7. **重测工具**：`tests/_rejudge.py`（`--today` / `--cid` / `--kaohao` / `--all`，默认预演、
   `--apply` 才写回）。写回时**不动 `tries` 与 `submitted_at`**（重测不是新交一次），
   旧分记在 `rejudge_from` 里，管理端能看出"这道题被重测过、原来多少分"。
   * 首次重测（今天那两场，79 份）：**分数变了 6 份、一致 73 份、失败 0 份**。
     变的那几份都是**学生自己写了 freopen** 的 —— 旧垫片把它们判低了，新引擎按
     `auto` 口径正确算分。这就是老师反馈的"有没有 freopen 结果不一样"。
8. **Hydro 已停**：`tests/_stop_hydro.sh`（容器 `stop` + 守卫服务 `disable --now`）。
   回退：`cd /root/hydro && sudo docker compose start`，再把这一轮的代码换回去。

## 还没做的

1. **测试**：`_e2e_mkproblem.sh` / `_e2e_rules.sh` 等一批断言直接查 Hydro
   （mongosh、`problem del`、"题目包落盘在 /root/hydro/..."），这一轮**没来得及改**，
   跑它们会红 —— 要改成查本地题目库（`data/problems/`）与 `localoj`。
   （冒烟 `_smoke_pages.sh` 与包内自测是纯路由/纯逻辑，仍然全绿。）
2. **文档**：《设计说明》里"题面真身在评测站""建题推给评测站"这些说法要跟着改；
   《运维手册》的备份/恢复、故障排查里 Hydro 相关条目也要改（判题日志从
   `docker logs oj-judge` 变成 `journalctl -u go-judge`）。
3. **`io_mode` 做成每场可配**：现在 CSP 固定 `auto`、OI/IOI 固定 `stdin`，
   想"按真实考场判"（漏 freopen 就是 0 分）还没有开关。
4. **题单导入（`importer.py`）**：那条路（CLI / HTTP 接口）还是老样子（生成 Hydro 题目包），
   现在没有 Hydro 可导了 —— 要改成写本地题目库。

## 环境备忘

* 沙箱地址可用 `CSP_GOJUDGE_URL` 覆盖（默认 `http://127.0.0.1:5050`）。
* 判题日志：`journalctl -u go-judge`；题目数据：`data/problems/`。
* 服务器到 GitHub 的**大文件下载会被掐断**（go-judge 二进制两次都断在 4.3M/17.3M），
  要么断点续传循环补齐，要么从 Docker 镜像里取 —— 别再傻等一次 `curl` 下完。
