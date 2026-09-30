#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题单批量导入接口 —— 把一份「出题工程」整包导入到评测站（Hydro）。

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
    python3 import_problemset.py <zip|目录> [选项]
    python3 import_problemset.py 题单.zip --tag 寒假作业 --dry-run
    python3 import_problemset.py 题单.zip --only G01,D01 --json 报告.json
    python3 import_problemset.py 题单.zip --code G01=candy --code DP01=t1

代码调用：
    from import_problemset import import_problemset
    report = import_problemset("题单.zip", tags=["寒假作业"], codes={"G01": "candy"})
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

# ---------------------------------------------------------------- 常量与规则

from ..config import HYDRO_COMPOSE_FILE, HYDRO_DIR

COMPOSE_DIR = HYDRO_DIR
COMPOSE = ["docker", "compose", "-f", HYDRO_COMPOSE_FILE]
HOST_IMPORT_DIR = "/root/hydro/data/backend/import"     # 宿主机上的导入目录
CONTAINER_IMPORT_DIR = "/root/.hydro/import"            # 容器内看到的同一目录

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

        if self.is_zip:
            self._zf = zipfile.ZipFile(source)
            self._names = [n.replace("\\", "/") for n in self._zf.namelist() if not n.endswith("/")]
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
            return self._zf.read(name)
        with open(os.path.join(self.root, name.replace("/", os.sep)), "rb") as f:
            return f.read()

    def stream_to(self, name: str, dst_path: str) -> None:
        """流式拷贝（大文件不进内存）。"""
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        if self._zf:
            with self._zf.open(name) as src, open(dst_path, "wb") as out:
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

PROBLEM_YAML = """title: {title}
pid: {pid}
tag:
{tags}
difficulty: {difficulty}
config: |-
  time: {time}
  memory: {memory}
"""


def write_package(bundle: Bundle, prob: dict, dest_root: str,
                  time_ms: int, memory_mb: int, extra_tags: list[str]) -> str:
    """把一道题写成 Hydro 题目包，返回包目录。"""
    root = os.path.join(dest_root, prob["pid"])
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "testdata"), exist_ok=True)
    os.makedirs(os.path.join(root, "std"), exist_ok=True)

    tags = list(dict.fromkeys([*extra_tags, *prob["topics"]])) or ["题单导入"]
    if prob["difficulty_label"]:
        tags.append(f"难度:{prob['difficulty_label']}")
    with open(os.path.join(root, "problem.yaml"), "w", encoding="utf-8") as f:
        f.write(PROBLEM_YAML.format(
            title=prob["title"], pid=prob["pid"], difficulty=prob["difficulty"],
            tags="\n".join(f"  - {t}" for t in tags),
            time=time_ms, memory=memory_mb,
        ))
    # 时限/内存的权威位置是 testdata/config.yaml（Hydro 导入器按这个文件名读取）；
    # 只写在 problem.yaml 的 config 字段里不会生效（实测踩过）。
    with open(os.path.join(root, "testdata", "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"time: {time_ms}\nmemory: {memory_mb}\n")

    statement = prob["statement"] or f"# {prob['title']}\n\n（本题单未附题面）\n"
    with open(os.path.join(root, "problem_zh.md"), "w", encoding="utf-8") as f:
        f.write(statement)

    # 测试点重排成 1.in/1.out、2.in/2.out……（原编号是两位补零，这里只保留顺序）
    for i, (_num, in_rel, out_rel) in enumerate(prob["cases"], 1):
        bundle.stream_to(in_rel, os.path.join(root, "testdata", f"{i}.in"))
        bundle.stream_to(out_rel, os.path.join(root, "testdata", f"{i}.out"))

    if prob["std"]:
        with open(os.path.join(root, "std", "solution.cpp"), "w", encoding="utf-8") as f:
            f.write(_decode(bundle.read(prob["std"])))
    return root


# ---------------------------------------------------------------- 导入 Hydro


def _container_path(host_path: str) -> str:
    """宿主机导入目录 -> 容器内路径（这个映射踩过坑，务必保持。"""
    if host_path.startswith(HOST_IMPORT_DIR):
        return CONTAINER_IMPORT_DIR + host_path[len(HOST_IMPORT_DIR):]
    return host_path


def hydro_problem_pids() -> set[str]:
    """站点上已有的题目标识（用于幂等判断）。"""
    js = 'print(db.document.find({docType:10},{pid:1}).toArray().map(d=>d.pid||"").join("\\n"))'
    try:
        p = subprocess.run(COMPOSE + ["exec", "-T", "oj-mongo", "mongosh", "hydro", "--quiet", "--eval", js],
                           cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return set()
    return {line.strip() for line in (p.stdout or "").splitlines() if line.strip()}


def hydro_delete_problem(pid: str) -> bool:
    """按 pid 删除题目（用于 --overwrite）。

    题**本来就不存在**（或评测站没起来）时返回 False，不抛异常——
    调用方经常是"先删掉旧的再导入"，题目不存在是正常情况。
    （踩过：mongosh 什么都没查到时会打印一个空行，旧写法 `.splitlines()[-1]`
    直接 IndexError，把验收脚本的准备阶段和整个导入流程带崩。）
    """
    js = (f'const d=db.document.findOne({{docType:10,pid:"{pid}"}},{{docId:1}}); '
          f'print(d ? d.docId : "")')
    try:
        p = subprocess.run(COMPOSE + ["exec", "-T", "oj-mongo", "mongosh", "hydro", "--quiet", "--eval", js],
                           cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        lines = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
        doc_id = lines[-1] if lines else ""
    except (subprocess.TimeoutExpired, OSError):
        return False
    if not doc_id:
        return False
    try:
        subprocess.run(COMPOSE + ["exec", "-T", "oj-backend", "hydrooj", "cli", "problem", "del", "system", doc_id],
                       cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return False
    return True


def hydro_import(pid: str, container_dir: str, timeout: int = 300) -> tuple[bool, str]:
    """调用站点自带的导入命令。"""
    cmd = COMPOSE + ["exec", "-T", "oj-backend", "hydrooj", "cli",
                     "problem", "import", "system", f"{container_dir}/{pid}"]
    try:
        p = subprocess.run(cmd, cwd=COMPOSE_DIR, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "导入超时"
    except OSError as e:
        return False, f"无法执行导入命令：{e}"
    blob = (p.stdout or "") + (p.stderr or "")
    if re.search(r"Imported problem\s+\S+", blob):
        return True, ""
    if re.search(r"\bError\b|ENOENT|Exception", blob):
        return False, blob.strip()[-300:]
    return p.returncode == 0, blob.strip()[-200:]


# ---------------------------------------------------------------- 主接口


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

        existing = set() if dry_run else hydro_problem_pids()
        if not dry_run:
            os.makedirs(HOST_IMPORT_DIR, exist_ok=True)

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
                entry["status"] = "skipped"
                entry["message"] = "站点上已有同标识的题目（加 --overwrite 可覆盖）"
                report["skipped"].append(pid)
                report["problems"].append(entry)
                log(f"  [已存在] {pid} {title}")
                continue

            # ---- 题目编号：一律由系统分配（T00001，落盘到题库）；
            #      这里能指定的只有**默认英文名**（学生侧），留空则加进比赛时再填
            want = (want_codes.get(pid.upper())
                    or want_codes.get(os.path.basename(prefix).upper()) or "")
            if not want and len(dirs) == 1:
                want = single_code
            try:
                prob["code"] = _mk.assign_number(pid, title=title, name=want)
            except _mk.CodeError as e:
                entry["status"] = "failed"
                entry["message"] = str(e)
                report["failed"].append(pid)
                report["problems"].append(entry)
                log(f"  [英文名不可用] {pid} {title}：{entry['message']}")
                continue
            prob["number"] = prob["code"]
            prob["name"] = _mk.name_of_pid(pid)
            entry["number"] = prob["number"]
            entry["name"] = prob["name"]
            entry["code"] = prob["number"]          # 兼容旧字段名
            report["codes"][pid] = prob["number"]
            report["names"][pid] = prob["name"]

            pkg = write_package(bundle, prob, tmpdir, time_ms, memory_mb, extra_tags)
            entry["package"] = pkg
            if dry_run:
                entry["status"] = "package-only"
                entry["message"] = "仅生成题目包（dry-run，未导入站点）"
                report["prepared"].append(pid)
                report["problems"].append(entry)
                log(f"  [试运行] {pid} {title}：编号 {prob['code']}，"
                    f"{len(prob['cases'])} 个测试点 -> {pkg}")
                continue

            # ---- 复制到站点的导入目录并调用导入命令
            target = os.path.join(HOST_IMPORT_DIR, pid)
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(pkg, target)
            if overwrite and pid in existing:
                hydro_delete_problem(pid)
            ok, msg = hydro_import(pid, CONTAINER_IMPORT_DIR)
            entry["status"] = "imported" if ok else "failed"
            entry["message"] = msg
            (report["imported"] if ok else report["failed"]).append(pid)
            report["problems"].append(entry)
            log(f"  [{'成功' if ok else '失败'}] {pid} {title}：{len(prob['cases'])} 个测试点"
                + (f"  {msg}" if msg else ""))
    finally:
        bundle.close()
        if not keep_workdir and not dry_run and workdir is None:
            shutil.rmtree(tmpdir, ignore_errors=True)

    report["elapsed_s"] = round(time.time() - started, 1)
    report["ok"] = not report["failed"]
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
  python3 import_problemset.py 题单.zip
  python3 import_problemset.py 题单.zip --tag 寒假作业 --time 2000 --memory 512
  python3 import_problemset.py ./题目库 --only G01,D01 --dry-run
  python3 import_problemset.py 题单.zip --code G01=candy --json /root/import-report.json
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
