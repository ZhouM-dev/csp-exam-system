# -*- coding: utf-8 -*-
"""端到端：HTTP 题单导入接口的 `code` 字段（= 题目默认英文名）。

为什么要单独一套：这条接口以前只能命令行传默认英文名，HTTP 传进来的题 `name`
是空的，得在「本场管理」里手工补。补上之后必须有测试盯着，否则哪天后台那段
`opts` 透传被删掉，界面照旧、只有导进来的题悄悄丢了英文名。

题单格式（importer 认的那种）：
    题单/
      G01-x/题目.md            题面
      G01-x/标程.cpp           标程
      G01-x/data/1.in|1.out    成对的数字命名测试点

⚠️ 别拿 `csp_exam/tools/make_problem_set.py` 当夹具：它生成的是 Hydro 原生题目包
（problem.yaml / problem_zh.md / testdata/），不是题单。喂给 importer 会被认成
24 道题全军覆没（踩过，白查半天）。

关键设计：文件夹后缀故意写成 `x` / `y`，而 `code` 字段传 `candy` / `road`。
接口没把 code 透传下去的话，报告里 name 会是空，一眼能看出来。

数据安全：`data/problem_codes.json` 前后快照并**在 finally 里还原**（dry_run 也会
登记编号，这是接口写在文档里的行为），本任务自己的 ps-jobs 临时目录一并清掉。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

R = "/root/csp-exam"
WS = "/tmp/pshttp"
URL = "http://127.0.0.1:8080"

BAD = 0
JOB = ""


def ok(cond, msg):
    global BAD
    print(("  [PASS] " if cond else "  [FAIL] ") + msg, flush=True)
    if not cond:
        BAD += 1


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


STD = """#include <bits/stdc++.h>
int main() { int a, b; std::cin >> a >> b; std::cout << a + b << std::endl; return 0; }
"""


def make_problem(root, statement, cases):
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(os.path.join(root, "题目.md"), "w", encoding="utf-8") as f:
        f.write(statement)
    with open(os.path.join(root, "标程.cpp"), "w", encoding="utf-8") as f:
        f.write(STD)
    for i, (inp, out) in enumerate(cases, 1):
        with open(os.path.join(root, "data", f"{i}.in"), "w", encoding="utf-8") as f:
            f.write(inp)
        with open(os.path.join(root, "data", f"{i}.out"), "w", encoding="utf-8") as f:
            f.write(out)


def cleanup(pc, before):
    """无论成败都要跑的收尾：还原登记表、清掉本次的 ps-jobs 临时目录。"""
    try:
        if before is not None:
            with open(pc, "wb") as f:
                f.write(before)
    except OSError as e:
        print(f"  [FAIL] 还原 problem_codes.json 失败：{e!r}")
        return
    for d in glob.glob(f"{R}/ps-jobs/{JOB}") + glob.glob(f"{R}/web/ps-jobs/{JOB}") + \
             glob.glob(f"{R}/csp_exam/web/ps-jobs/{JOB}"):
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(WS, ignore_errors=True)


def main():
    global JOB, BAD
    print("=== HTTP 题单导入：code 字段（默认英文名） ===", flush=True)

    pc = f"{R}/data/problem_codes.json"
    before = None
    if os.path.exists(pc):
        before = open(pc, "rb").read()

    try:
        shutil.rmtree(WS, ignore_errors=True)
        os.makedirs(f"{WS}/set", exist_ok=True)

        print("-- 1. 造一份合规题单")
        make_problem(f"{WS}/set/G01-x", "# 加法\n\n输入两个整数，输出它们的和。\n",
                     [("1 2\n", "3\n"), ("5 7\n", "12\n")])
        make_problem(f"{WS}/set/G02-y", "# 减法\n\n输入两个整数，输出它们的差。\n",
                     [("5 3\n", "2\n"), ("9 4\n", "5\n")])
        with zipfile.ZipFile(f"{WS}/set.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _d, files in os.walk(f"{WS}/set"):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    z.write(full, os.path.relpath(full, f"{WS}/set"))
        ok(os.path.getsize(f"{WS}/set.zip") > 0,
           f"题单 zip {os.path.getsize(f'{WS}/set.zip')} 字节")

        key = open(f"{R}/data/admin_key.txt").read().strip()

        print("-- 2. 对照组：CLI 直跑（不带 --code）")
        cli_run = sh(f"cd {R} && python3 -m csp_exam.core.importer {WS}/set --dry-run 2>&1")
        cli_nums = {}
        for line in cli_run.stdout.splitlines():
            if "[试运行]" in line:
                parts = line.split()
                if len(parts) > 3:            # [试运行] G01 x：编号 T00012，2 个测试点 -> ...
                    cli_nums[parts[1]] = parts[3].split("，")[0]
        ok(len(cli_nums) == 2, f"CLI 识别出 2 道题并拿到编号：{cli_nums}")

        print("-- 3. POST 到 HTTP 接口（code=G01=candy,G02=road）")
        r = sh(f'curl -s -X POST "{URL}/api/problemset/import?key={key}" '
               f'-F "file=@{WS}/set.zip" -F "dry_run=1" -F "code=G01=candy,G02=road"')
        try:
            resp = json.loads(r.stdout)
        except ValueError:
            resp = {}
        ok(resp.get("ok") is True, f"接口返回 ok=true（{r.stdout.strip()[:120]}）")
        JOB = resp.get("job", "")

        print("-- 4. 轮询报告")
        report = {}
        for _ in range(60):
            time.sleep(1)
            q = sh(f'curl -s "{URL}/api/problemset/report?key={key}&job={JOB}"')
            try:
                report = json.loads(q.stdout)
            except ValueError:
                continue
            if report.get("state") in ("done", "failed"):
                break
        ok(report.get("state") == "done",
           f"任务结束 state={report.get('state')}（{report.get('progress')}）")
        rep = report.get("report") or {}

        print("-- 5. 英文名有没有真的透传进来")
        got = {str(i.get("pid")): i.get("name", "") for i in (rep.get("problems") or [])}
        for pid, name in (("G01", "candy"), ("G02", "road")):
            ok(got.get(pid) == name, f"{pid} 的默认英文名 = {name!r}（实际 {got.get(pid)!r}）")

        print("-- 6. 落盘 + 编号幂等")
        raw = json.loads(open(pc, encoding="utf-8").read()) if os.path.exists(pc) else {}
        codes = raw.get("items") or raw
        for pid, name in (("G01", "candy"), ("G02", "road")):
            rec = codes.get(pid) or {}
            ok(rec.get("name") == name,
               f"problem_codes.json[{pid}].name = {name!r}（实际 {rec.get('name')!r}）")
            ok(cli_nums.get(pid) == rec.get("code"),
               f"{pid} 编号与 CLI 那次一致（{rec.get('code')!r}）")
    finally:
        cleanup(pc, before)
        if os.path.exists(pc) and before is not None:
            ok(open(pc, "rb").read() == before, "problem_codes.json 已还原成测试前的内容")

    print()
    if BAD:
        print(f"HTTP 题单导入：{BAD} 项失败")
        return 1
    print("HTTP 题单导入：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
