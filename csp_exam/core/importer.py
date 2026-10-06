#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题单批量导入接口 —— 把一份「出题工程」整包导入到本地题库。

输入：一个 zip 或目录，结构遵循出题工程规范（详见 题目单导入接口.md）：
    <题目库>/
      _索引.md                     # 可选：编号/分类号/题名/难度/考点 表
      <分类号>-<题名>/
        题目.md                    # 题面（Markdown）
        标程.cpp                   # 标准程序
        data/01.in 01.out …        # 测试点：两位数字命名，成对出现
        data/样例1.in/out          # 题目样例（按规范不参与评测，自动忽略）
        大样例/、暴力.cpp、生成器.py …  # 其它附属文件，自动忽略

输出：把每道题导入站点，返回结构化报告（JSON）。

题目编号（老师侧，形如 `T00001`）在**建题 / 导入时由系统分配**、跟着题走；老师在界面上
只用它定位查找，学生看不到它。学生看到的是**英文名**（如 `candy`）——在本场配题时设置，
用来建文件夹、命名源文件、写 freopen。
出题工程里的「分类号」（`G01`、`DP01`）只是**题库内部标识**，不再当学生看到的编号。
导入时可以顺手给某道题一个**默认英文名**（`--code G01=candy`；留空则加进比赛时再填），
分配与合法性校验都走 `problems.assign_number`，和自己建题同一套逻辑。

命令行：
    python3 -m csp_exam.core.importer <zip|目录> [选项]
    python3 -m csp_exam.core.importer 题单.zip --tag 寒假作业 --dry-run
    python3 -m csp_exam.core.importer 题单.zip --only G01,D01 --json 报告.json
    python3 -m csp_exam.core.importer 题单.zip --code G01=candy --code DP01=t1

代码调用：
    from csp_exam.core.importer import import_problemset
    report = import_problemset("题单.zip", tags=["寒假作业"], codes={"G01": "candy"})
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

# ---------------------------------------------------------------- 常量与规则

DEFAULT_TIME_MS = 1000
DEFAULT_MEMORY_MB = 256
DEFAULT_TAGS = ["题单导入"]

#: 题面候选文件名（按优先级）
STATEMENT_NAMES = ("题目.md", "题面.md", "statement.md", "problem.md", "README.md")
#: 标程候选文件名（按优先级）；"暴力"之类一律不当标程
STD_NAMES = ("标程.cpp", "std.cpp", "solution.cpp", "ac.cpp", "correct.cpp")
#: 测试数据目录候选名
DATA_DIRS = ("data", "testdata", "tests", "test")
#: 分类号：1~3 个大写字母 + 两位数字，如 G01 / DP01 / ST04
CLASS_CODE_RE = re.compile(r"^([A-Z]{1,3}\d{2})-(.+)$")
#: 测试点文件：两位数字（也容忍 game001 这种纯数字尾号）
CASE_FILE_RE = re.compile(r"^(\d+)\.in$", re.I)

#: 洛谷 7 级难度标签 -> 站点难度数值（标签原样保留到 tag 里）
#: 注意顺序：复合标签必须排在宽泛标签前面，否则「提高+/省选−」会被「省选−」抢先匹配
DIFFICULTY_MAP = [
    ("提高+/省选", 5),      # 提高+/省选−
    ("省选/NOI", 6),       # 省选/NOI−
    ("省选", 6),           # 省选、省选−
    ("NOI", 7),            # NOI/NOI+/CTS
    ("CTS", 7),
    ("普及+/提高", 4),
    ("普及/提高", 3),
    ("普及", 2),
    ("入门", 1),
]


def difficulty_of(label: str) -> int:
    """把洛谷难度标签映射成 1~7；认不出来给 3。"""
    if not label:
        return 3
    for key, value in DIFFICULTY_MAP:
        if key in label:
            return value
    return 3


def split_topics(text: str) -> list[str]:
    """考点列按规范用全角竖线 ｜ 分隔（也容忍半角 | 和顿号）。"""
    if not text:
        return []
    parts = re.split(r"[｜|、,，/]", text)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------- 输入读取


class Bundle:
    """把 zip 或目录统一成「路径 -> 读字节」的接口。"""

    def __init__(self, source: str):
        self.source = source
        self.is_zip = os.path.isfile(source) and source.lower().endswith(".zip")
        self._zf: zipfile.ZipFile | None = None
        self.root = source
        self._names: list[str] = []
        self._members = {}

        if self.is_zip:
            self._zf = zipfile.ZipFile(source)
            try:
                total = 0
                for member in self._zf.infolist():
                    if member.is_dir(): continue
                    name = member.filename.replace("\\", "/")
                    if name.startswith("/") or ":" in name or ".." in name.split("/") or "\x00" in name:
                        raise ValueError("题单 zip 包含越界文件名")
                    if name in self._members: raise ValueError("题单 zip 包含重复文件名")
                    self._members[name] = member
                    total += member.file_size
                    if len(self._members) > 20000 or total > 512 * 1024 * 1024:
                        raise ValueError("题单解压后超过 512MB 或 20000 个文件")
                self._names = list(self._members)
            except Exception:
                self._zf.close()
                raise
        elif os.path.isdir(source):
            for base, _dirs, files in os.walk(source):
                for f in files:
                    full = os.path.join(base, f)
                    self._names.append(os.path.relpath(full, source).replace("\\", "/"))
        else:
            raise FileNotFoundError(f"既不是 zip 也不是目录：{source}")

    def names(self) -> list[str]:
        return list(self._names)

    def read(self, name: str) -> bytes:
        if self._zf:
            return self._zf.read(self._members[name])
        with open(os.path.join(self.root, name.replace("/", os.sep)), "rb") as f:
            return f.read()

    def stream_to(self, name: str, dst_path: str) -> None:
        """流式拷贝（大文件不进内存）。"""
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        if self._zf:
            with self._zf.open(self._members[name]) as src, open(dst_path, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 256)
        else:
            shutil.copyfile(os.path.join(self.root, name.replace("/", os.sep)), dst_path)

    def close(self) -> None:
        if self._zf:
            self._zf.close()


# ---------------------------------------------------------------- 题单解析


def find_problem_dirs(bundle: Bundle) -> list[tuple[str, str, str]]:
    """找出所有题目目录，返回 [(目录前缀, 分类号, 题名)]。

    优先按「分类号-题名」识别；同时兼容"目录里有 data/*.in 但没有分类号"的普通题包。
    """
    names = bundle.names()
    prefixes: dict[str, set[str]] = {}
    for n in names:
        parts = n.split("/")
        # 从最深的父目录往上找，取真正含题的那个目录
        for depth in range(len(parts) - 1, 0, -1):
            prefix = "/".join(parts[:depth])
            prefixes.setdefault(prefix, set()).add("/".join(parts[depth:]))

    # 只保留"含题面或含数据"的目录，且取最浅的那个（避免把子目录也当成题）
    found: dict[str, tuple[str, str]] = {}
    for prefix, children in sorted(prefixes.items()):
        # 只认「直接放在这个目录里」的特征文件——否则外层容器（题目库/）会因为
        # 里面有 "G01-x/01.in" 这种带斜杠的子路径而被误判成一道题，
        # 进而把真正的题目目录当成"更深的子目录"过滤掉（踩过：整包只识别出一道 P 题）。
        direct = {c for c in children if "/" not in c}
        has_stmt = any(c in STATEMENT_NAMES for c in direct)
        has_std = any(c in STD_NAMES for c in direct)
        has_data = any(c.endswith(".in") for c in direct) or any(
            c in DATA_DIRS for c in direct)
        if not (has_stmt or has_data or has_std):
            continue
        base = os.path.basename(prefix)
        m = CLASS_CODE_RE.match(base)
        if m:
            found[prefix] = (m.group(1), m.group(2))
        else:
            # 没有分类号：用目录名当 pid（清洗成字母数字），题名就是目录名
            pid = re.sub(r"[^0-9A-Za-z]+", "", base)[:20] or "P"
            found[prefix] = (pid, base)

    # 去掉被更浅目录包含的（比如 题目/D01-x/ 与 题目/D01-x/data/ 同时匹配）
    result = []
    for prefix in sorted(found):
        if any(prefix != other and prefix.startswith(other + "/") for other in found):
            continue
        result.append((prefix, found[prefix][0], found[prefix][1]))
    return result


def parse_index(bundle: Bundle) -> dict[str, dict]:
    """解析 _索引.md 里的表格（编号/分类号/题名/难度/考点/测试点/大样例/状态）。"""
    info: dict[str, dict] = {}
    for name in bundle.names():
        if os.path.basename(name) not in ("_索引.md", "_index.md"):
            continue
        try:
            text = bundle.read(name).decode("utf-8")
        except UnicodeDecodeError:
            text = bundle.read(name).decode("gbk", errors="replace")
        for line in text.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or cells[0] in ("编号", "---") or set(cells[0]) <= {"-"}:
                continue
            code = cells[1] if len(cells) > 1 else ""
            if not CLASS_CODE_RE.match(code + "-x"):
                continue
            info[code] = {
                "编号": cells[0],
                "题名": cells[2] if len(cells) > 2 else "",
                "难度": cells[3] if len(cells) > 3 else "",
                "考点": cells[4] if len(cells) > 4 else "",
                "测试点": cells[5] if len(cells) > 5 else "",
            }
        break
    return info


def parse_problem(bundle: Bundle, prefix: str, pid: str, title: str,
                  index_info: dict) -> dict:
    """解析单道题：题面、数据、标程。"""
    children = {}
    for n in bundle.names():
        if n.startswith(prefix + "/"):
            children[n[len(prefix) + 1:]] = n

    # ---- 题面
    statement = ""
    statement_from = ""
    for cand in STATEMENT_NAMES:
        if cand in children:
            raw = bundle.read(children[cand])
            statement = _decode(raw)
            statement_from = cand
            break

    # ---- 测试数据：data/ 或题目根目录下的 <数字>.in + 同名 .out
    pairs: list[tuple[int, str, str]] = []
    for dirname in ("",) + DATA_DIRS:
        base = dirname + "/" if dirname else ""
        got = []
        for rel, full in children.items():
            if not rel.startswith(base):
                continue
            tail = rel[len(base):]
            if "/" in tail:                     # 只看这一层，别钻子目录（大样例等）
                continue
            m = CASE_FILE_RE.match(tail)
            if not m:
                continue
            num = int(m.group(1))
            out_rel = base + os.path.splitext(tail)[0] + ".out"
            if out_rel in children:
                got.append((num, full, children[out_rel]))
        if got:
            pairs = sorted(got)
            break

    # ---- 标程
    std_rel = ""
    for cand in STD_NAMES:
        if cand in children:
            std_rel = children[cand]
            break

    # ---- 索引里的元数据
    meta = index_info.get(pid, {})
    return {
        "pid": pid,
        "title": title,
        "prefix": prefix,
        # 题目编号（老师侧的 T00001）与英文名（学生侧，如 candy）都在导入时定下来：
        # 编号由 assign_number() 分配，英文名由 --code 指定、留空则加进比赛时填。
        # `pid`（分类号）只是题库内部标识，两个都不给它。
        "code": "",
        "name": "",
        "statement": statement,
        "statement_from": statement_from,
        "cases": pairs,
        "std": std_rel,
        "difficulty_label": meta.get("难度", ""),
        "difficulty": difficulty_of(meta.get("难度", "")),
        "topics": split_topics(meta.get("考点", "")),
        "index_no": meta.get("编号", ""),
        "warnings": [],
    }


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 生成题目包

def import_problemset(source: str, *, tags: list[str] | None = None,
                      time_ms: int = DEFAULT_TIME_MS, memory_mb: int = DEFAULT_MEMORY_MB,
                      only: list[str] | None = None, overwrite: bool = False,
                      dry_run: bool = False, workdir: str | None = None,
                      keep_workdir: bool = False, code: str = "",
                      codes: dict[str, str] | None = None, log=print) -> dict:
    """把一份题单（zip 或目录）导入评测站。

    code  —— 默认**英文名**，只在**这次导入恰好一道题**时用得上（如 `--code candy`）
    codes —— 逐题指定默认英文名：{分类号: 英文名}（如 `{"G01": "candy"}`）；没指定的题
             加进比赛时再填。**题目编号一律由系统分配**（`T00001`），走
             `problems.assign_number`，与自己建题是同一套逻辑；
             导入结果里每题都有 `number`（编号）与 `name`（英文名）两个字段。

    返回报告：{"total": N, "imported": [...], "skipped": [...], "failed": [...], "problems": [...]}
    """
    from .store import valid_name
    from . import problems as _mk          # 延迟导入：编号分配/校验的唯一实现在那边
    extra_tags = list(tags or []) or ["题单导入"]
    started = time.time()
    bundle = Bundle(source)
    tmpdir = workdir or tempfile.mkdtemp(prefix="problemset-")
    report: dict = {
        "source": os.path.abspath(source),
        "workdir": tmpdir,
        "dry_run": dry_run,
        "total": 0, "imported": [], "prepared": [], "skipped": [], "failed": [],
        "problems": [], "codes": {}, "names": {},
    }
    single_code = str(code or "").strip()
    want_codes = {str(k or "").strip().upper(): str(v or "").strip()
                  for k, v in (codes or {}).items() if str(k or "").strip()}
    try:
        index_info = parse_index(bundle)
        dirs = find_problem_dirs(bundle)
        if only:
            wanted = {w.upper() for w in only}
            dirs = [d for d in dirs if d[1].upper() in wanted]
        report["total"] = len(dirs)
        if single_code and len(dirs) > 1:
            report["error"] = (f"这次要导入 {len(dirs)} 道题，一个编号不够用；"
                               f"请按「分类号=编号」逐题指定，如 --code G01=candy")
            report["elapsed_s"] = round(time.time() - started, 1)
            report["ok"] = False
            return report
        log(f"识别到 {len(dirs)} 道题" + (f"（索引里 {len(index_info)} 条元数据）" if index_info else ""))

        existing = _mk.localoj.problem_pids()
        for prefix, pid, title in dirs:
            prob = parse_problem(bundle, prefix, pid, title, index_info)
            entry = {
                "pid": pid, "title": title, "case_count": len(prob["cases"]),
                "difficulty": prob["difficulty"], "difficulty_label": prob["difficulty_label"],
                "topics": prob["topics"], "has_statement": bool(prob["statement"]),
                "has_std": bool(prob["std"]), "index_no": prob["index_no"],
                "code": "", "status": "", "message": "",
            }
            # ---- 基本校验
            problems = []
            if not prob["cases"]:
                problems.append("没有找到成对的 数字.in / 数字.out 测试数据")
            if not prob["statement"]:
                problems.append("没有找到题面（题目.md）")
            if not prob["std"]:
                problems.append("没有找到标程（标程.cpp）")
            if problems:
                entry["status"] = "failed"
                entry["message"] = "；".join(problems)
                report["failed"].append(pid)
                report["problems"].append(entry)
                log(f"  [跳过] {pid} {title}：{entry['message']}")
                continue

            if pid in existing and not overwrite:
                entry.update(status="skipped", message="题库已有同标识的题目；覆盖请启用 overwrite。")
                report["skipped"].append(pid)
                report["problems"].append(entry)
                continue
            # ---- 题目编号：一律由系统分配（T00001，落盘到题库）；
            #      这里能指定的只有**默认英文名**（学生侧），留空则加进比赛时再填
            want = (want_codes.get(pid.upper())
                    or want_codes.get(os.path.basename(prefix).upper()) or "")
            if not want and len(dirs) == 1:
                want = single_code
            if dry_run:
                if want and not valid_name(want):
                    entry.update(status="failed", message=_mk.CODE_HINT)
                    report["failed"].append(pid)
                else:
                    entry.update(status="validated", message="校验通过，未写入题库")
                    report["prepared"].append(pid)
                report["problems"].append(entry)
                continue
            uploads = {n: bundle.read(n) for n in bundle.names() if n.startswith(prefix + "/")}
            got = _mk.create_problem(pid, title, uploads, time_ms=time_ms, memory_mb=memory_mb,
                                     statement=prob["statement"], std_source=_decode(bundle.read(prob["std"])),
                                     overwrite=overwrite, name=want, tags=extra_tags + prob["topics"])
            ok = bool(got.get("ok"))
            entry.update(status="imported" if ok else "failed", message=got.get("error", ""),
                         number=got.get("number", ""), code=got.get("number", ""),
                         name=got.get("name", ""), pid=got.get("pid", pid))
            (report["imported"] if ok else report["failed"]).append(entry["pid"])
            report["problems"].append(entry)
            if ok:
                existing.add(entry["pid"])
                report["codes"][entry["pid"]] = entry["number"]
                report["names"][entry["pid"]] = entry["name"]
            log(f"  [{'成功' if ok else '失败'}] {pid} {title}：{entry['message']}")
    finally:
        bundle.close()
        if not keep_workdir and workdir is None:
            shutil.rmtree(tmpdir, ignore_errors=True)

    report["elapsed_s"] = round(time.time() - started, 1)
    report["ok"] = bool(report["total"]) and not report["failed"]
    if not report["total"]: report["error"] = "没有识别出任何题目目录"
    return report


# ---------------------------------------------------------------- 命令行


def parse_codes(items) -> tuple[str, dict[str, str]]:
    """把 `[分类号=]英文名` 形式的若干条写法拆成 `(整体英文名, {分类号: 英文名})`。

    - `"G01=candy"` → 逐题：`("", {"G01": "candy"})`
    - `"candy"`     → 整体：整包只有一道题时用得上

    命令行 `--code`（可重复）和 HTTP 接口的 `code` 字段（逗号分隔）共用同一套语义，
    免得两边写法各长一样。
    """
    single, codes = "", {}
    for item in items or []:
        pid, sep, val = str(item).partition("=")
        if sep:
            if pid.strip():
                codes[pid.strip()] = val.strip()
        else:
            single = pid.strip() or single
    return single, codes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="题单批量导入接口：把出题工程（zip 或目录）整包导入评测站",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python3 -m csp_exam.core.importer 题单.zip
  python3 -m csp_exam.core.importer 题单.zip --tag 寒假作业 --time 2000 --memory 512
  python3 -m csp_exam.core.importer ./题目库 --only G01,D01 --dry-run
  python3 -m csp_exam.core.importer 题单.zip --code G01=candy --json /root/import-report.json
""")
    ap.add_argument("source", help="题单 zip 或目录")
    ap.add_argument("--tag", action="append", default=[], help="额外打上的标签，可重复")
    ap.add_argument("--time", type=int, default=DEFAULT_TIME_MS, help=f"时限（毫秒，默认 {DEFAULT_TIME_MS}）")
    ap.add_argument("--memory", type=int, default=DEFAULT_MEMORY_MB, help=f"内存（MB，默认 {DEFAULT_MEMORY_MB}）")
    ap.add_argument("--only", default="", help="只导入指定的题目标识，逗号分隔（如 G01,D01）")
    ap.add_argument("--code", action="append", default=[], metavar="[分类号=]英文名",
                    help="指定这道题的默认**英文名**（可重复）：--code G01=candy 按分类号指定；"
                         "整包只有一道题时可以直接 --code candy。题目编号由系统分配（T00001），"
                         "不在这里指定；英文名留空则加进比赛时再填")
    ap.add_argument("--overwrite", action="store_true", help="站点上已存在同标识题目时先删除再导入")
    ap.add_argument("--dry-run", action="store_true", help="只生成题目包并报告，不导入站点")
    ap.add_argument("--workdir", default=None, help="题目包输出目录（默认临时目录）")
    ap.add_argument("--json", default=None, help="把报告写到指定 JSON 文件")
    args = ap.parse_args(argv)

    single_code, codes = parse_codes(args.code)

    report = import_problemset(
        args.source, tags=args.tag, time_ms=args.time, memory_mb=args.memory,
        only=[x for x in args.only.split(",") if x.strip()], overwrite=args.overwrite,
        dry_run=args.dry_run, workdir=args.workdir, keep_workdir=bool(args.workdir),
        code=single_code, codes=codes,
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告已写入", args.json)
    print()
    if report.get("error"):
        print("导入未开始：", report["error"])
    if report["dry_run"]:
        print(f"共 {report['total']} 道题：已生成题目包 {len(report['prepared'])}、"
              f"校验失败 {len(report['failed'])}，耗时 {report['elapsed_s']}s（试运行，未导入站点）")
    else:
        print(f"共 {report['total']} 道题：成功 {len(report['imported'])}、"
              f"已存在跳过 {len(report['skipped'])}、失败 {len(report['failed'])}，"
              f"耗时 {report['elapsed_s']}s")
    if report.get("codes"):
        print("题目编号（老师侧）：", "、".join(f"{k}→{v}" for k, v in report["codes"].items()))
    if report.get("names"):
        print("默认英文名（学生侧）：", "、".join(f"{k}→{v or '（待填）'}"
                                                for k, v in report["names"].items()))
    if report["failed"]:
        print("失败列表：", "、".join(report["failed"]))
        return 1
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
