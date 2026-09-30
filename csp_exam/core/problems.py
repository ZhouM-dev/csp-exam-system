#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在管理端「直接建一道题」：写题目包 + 上传样例/测试数据 + 导入评测站。

和 import_problemset.py 的区别：那个是**整包导入出题工程**，这个是**临时新建一道题**
（或者给已有题目换/补数据），面向"老师现场出一道题"的场景。

数据配对规则（尽量宽容，但要能说清哪组没配上）：
    1.in  + 1.out / 1.ans        -> 第 1 组
    a.in  + a.ans                -> 第 2 组
    sample1.in + sample1.out     -> 第 3 组
    （按名字自然排序：2 排在 10 前面）
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import zipfile

from . import importer as ip

PROBLEM_YAML = """title: {title}
pid: {pid}
tag:
{tags}
difficulty: {difficulty}
"""

DEFAULT_TIME_MS = 1000
DEFAULT_MEMORY_MB = 256


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------- 题目编号 / 英文名
#
# 两个概念，别混：
#
#   * **题目编号**（`code`，形如 `T00001`）—— 老师侧用。**建题时系统分配、跟着题走**，
#     之后加进哪场比赛、排第几个都不变，老师不能改；老师只用它定位查找
#     （「T00001 那道题」）。学生看不到它。
#   * **英文名**（`name`，形如 `candy`、`m02`）—— 学生侧用。老师在**新建 / 管理考试**
#     时设置（本场 `exam.json` 里每道题的 `name`），学生拿它建文件夹、命名源文件、
#     写 `freopen("candy.in")`。只允许小写字母、数字、下划线。
#
# 出题工程里的「分类号」（`G01`、`DP01`）只是**题库内部标识**（Hydro 的 pid），
# 老师界面不再出现，学生更看不到。
#
# 登记落盘在 `data/problem_codes.json`：
#     {题库标识: {"code": "T00001", "name": "candy", "title": "...", "at": "..."}}
# `name` 是**建题时填的默认英文名**（可留空）：加进比赛时用它当输入框的默认值，
# 老师可以在本场管理里按本场情况改。读取（含老数据兜底）用
# `store.number_of(prob)`（编号）/ `store.code_of(prob)`（英文名）。

#: 英文名不合法时给老师看的话
CODE_HINT = "英文名只能用英文小写字母、数字、下划线，比如 candy、m02"
#: 编号登记表（题库里每道题分到的编号与默认英文名）
CODE_FILE = "problem_codes.json"
#: 系统分配的题目编号形如 T00001（T + 5 位序号）。
#: 也认老格式 `T1`（前导零随意），免得算「下一个序号」时漏掉老数据。
_SYS_CODE_RE = re.compile(r"^[Tt]0*(\d+)$")


class CodeError(ValueError):
    """英文名不合法，或者编号分配失败（消息可直接给老师看）。"""


def codes_path() -> str:
    """编号登记表的路径（data/problem_codes.json）。"""
    from . import store
    return os.path.join(store.DATA_DIR, CODE_FILE)


def load_codes() -> dict:
    """题库里每道题的编号与默认英文名：{题库标识: {"code", "name", "title", "at"}}。"""
    from . import store
    data = store._load(codes_path(), dict) or {}
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def save_codes(items: dict) -> None:
    from . import store
    with store._LOCK:
        store._save(codes_path(), {"items": items,
                                   "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def code_of_pid(pid: str, items: dict | None = None) -> str:
    """这道题（题库标识/分类号）的**题目编号**（`T00001`）；还没编号就返回 ""。"""
    rec = (load_codes() if items is None else items).get(str(pid or "").strip()) or {}
    return str(rec.get("code") or "").strip()


def number_of_pid(pid: str, items: dict | None = None) -> str:
    """`code_of_pid` 的新叫法（返回的是**题目编号** T00001，不是英文名）。"""
    return code_of_pid(pid, items)


def name_of_pid(pid: str, items: dict | None = None) -> str:
    """这道题**建题时填的默认英文名**（可留空）；没填过返回 ""。

    它只是加进比赛时输入框的默认值 —— 学生真正看到的英文名在**本场** `exam.json`
    的 `name` 字段里（老师配题时可以按本场情况改）。
    """
    rec = (load_codes() if items is None else items).get(str(pid or "").strip()) or {}
    return str(rec.get("name") or "").strip()


def code_owner(code: str, items: dict | None = None) -> str:
    """哪个题占了这个**编号**（返回它的题库标识；没人占返回 ""）。"""
    want = str(code or "").strip().upper()
    if not want:
        return ""
    for pid, rec in (load_codes() if items is None else items).items():
        if str((rec or {}).get("code") or "").strip().upper() == want:
            return str(pid)
    return ""


def _owner_text(items: dict, pid: str) -> str:
    title = str((items.get(pid) or {}).get("title") or "").strip()
    return f"{pid} {title}".strip()


def next_number(items: dict | None = None) -> str:
    """按规范取下一个**题目编号**：题库里现有编号的最大序号 + 1，形如 `T00006`。"""
    items = load_codes() if items is None else items
    top = 0
    for rec in items.values():
        m = _SYS_CODE_RE.match(str((rec or {}).get("code") or "").strip())
        if m:
            top = max(top, int(m.group(1)))
    n = top + 1
    while code_owner(f"T{n:05d}", items):       # 兜底：序号被人手工占过
        n += 1
    return f"T{n:05d}"


def next_code(items: dict | None = None) -> str:
    """兼容旧名：等价于 `next_number`（编号一律 `T` + 5 位数字）。"""
    return next_number(items)


def check_name(name: str, pid: str = "") -> str:
    """服务端校验**英文名**（老师在本场配题时填的那个）。不合法抛 `CodeError`。

    只校验格式：英文名是**按场次**用的（同一道题在不同场次可以叫不同的名字），
    所以不在题库层面查重 —— 同一场里重名由配题那边处理。
    `pid` 只是保留参数（老的 `check_code(pid)` 调用点不用改）。
    """
    from . import store
    want = str(name or "").strip()
    if not store.valid_code(want):
        raise CodeError(CODE_HINT)
    return want


def check_code(code: str, pid: str = "") -> str:
    """兼容旧名：等价于 `check_name`（校验的是英文名）。"""
    return check_name(code, pid)


def assign_number(pid: str, title: str = "", name: str = "") -> str:
    """**建题/导入时**给这道题分配一个**题目编号**（`T00001`），并记进题库。

    返回最终编号（形如 `T00001`）。

    * 这道题**已经有编号**（`T00001` 形式）→ 沿用原来的（重复导入 / 题单导入勾了 `overwrite` 也不会换编号）
    * 没有编号、或者存的还是老值（英文名 `candy` / 老式短编号 `T1`）→ 取下一个序号
      （现有最大序号 + 1）**升级**掉
    * 编号一律由系统分配，**老师不能自己定**

    `name` 是可选的教学方便字段：建题时填的**默认英文名**，加进比赛时当默认值。
    `name` 不合法（大写、短横、中文…）会抛 `CodeError`。
    """
    from . import store
    pid = str(pid or "").strip()
    if not pid:
        raise CodeError("这道题还没有题库标识（分类号），分配不了编号")
    want_name = str(name or "").strip()
    if want_name:
        check_name(want_name)
    with store._LOCK:
        items = load_codes()
        rec = items.get(pid) or {}
        code = str(rec.get("code") or "").strip()
        if code and not store.is_problem_number(code):
            # 老数据里存的是英文名（candy）或老式短编号（T1）→ 升级成 `T00001` 形式。
            # 这样即使迁移脚本漏了哪条记录，只要这道题被保存/导入一次就自动补上。
            code = ""
        if not code:
            code = next_number(items)
        items[pid] = {
            "code": code,
            "name": want_name or str(rec.get("name") or "").strip(),
            "title": str(title or rec.get("title") or "").strip(),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_codes(items)
        return code


def assign_code(pid: str, wanted: str = "", title: str = "") -> str:
    """兼容旧名：等价于 `assign_number`。

    以前 `wanted` 是老师指定的**编号**（如 `candy`）；现在编号由系统分配，
    这个位置改成**默认英文名**（见 `assign_number` 的 `name`）。
    """
    return assign_number(pid, title=title, name=wanted)


# ---------------------------------------------------------------- 出题工程结构识别

#: 大样例目录的候选名（出题工程里通常就叫「大样例」）
BIG_SAMPLE_DIRS = ("大样例", "大样例数据", "sample", "samples", "bigsample", "big_sample")
#: 题目样例的文件名前缀（data/样例1.in 这种，规范里"不参与评测"）
SAMPLE_STEMS = ("样例", "sample", "ex", "example")


class MemoryBundle:
    """把 {相对路径: 字节} 伪装成 import_problemset.Bundle 的接口，好复用它的识别逻辑。"""

    def __init__(self, files: dict[str, bytes]):
        self._files = {k.replace("\\", "/"): v for k, v in files.items()}

    def names(self) -> list[str]:
        return list(self._files)

    def read(self, name: str) -> bytes:
        return self._files[name]

    def stream_to(self, name: str, dst_path: str) -> None:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(dst_path, "wb") as f:
            f.write(self._files[name])

    def close(self) -> None:
        pass


def _pair_up(rels: dict[str, str]) -> list[dict]:
    """把一批文件按同名配成 in/out 对。rels = {相对路径: 相对路径}。

    返回 [{"stem","label","in","out","extra":[...]}]。
    """
    ins, outs, others = {}, {}, []
    for rel, full in rels.items():
        base = os.path.basename(rel)
        low = base.lower()
        ext = os.path.splitext(base)[1]          # ".in" / ".out" / ".ans"
        if low.endswith(".in"):
            ins[rel[: -len(".in")]] = full
        elif low.endswith((".out", ".ans")):
            outs[rel[: -len(ext)]] = full        # 只剥扩展名（多剥会把中文名切空）
        else:
            others.append(full)
    items = []
    for stem in sorted(set(ins) | set(outs), key=_natural_key):
        items.append({
            "stem": stem,
            "label": os.path.basename(stem) or stem,
            "in": ins.get(stem),
            "out": outs.get(stem),
            "extra": [],
        })
    if others:
        items.append({"stem": "", "label": "其它文件", "in": None, "out": None, "extra": others})
    return items


def collect_samples(children: dict[str, str], pid: str) -> tuple[list[dict], list[dict], list[str]]:
    """从一道题的文件里挑出大样例与题目样例。

    children = {题目目录内的相对路径: 全路径}
    返回 (大样例, 题目样例, 说明)。大样例单独成组（学生可下载），样例是题面里给的。
    """
    notes: list[str] = []
    big_rels: dict[str, str] = {}
    small_rels: dict[str, str] = {}
    prefix_low = tuple(s.lower() for s in SAMPLE_STEMS)
    for rel, full in children.items():
        parts = rel.split("/")
        base = os.path.basename(rel)
        # ① 大样例：放在 大样例/（或 sample/ 等）子目录里
        if len(parts) > 1 and parts[0] in BIG_SAMPLE_DIRS:
            big_rels["/".join(parts[1:])] = full
            continue
        # ② 题目样例：文件名以 样例/sample/ex/example 开头（放在根目录或 data/ 下都认）
        if base.lower().endswith((".in", ".out", ".ans")):
            stem = base.rsplit(".", 1)[0].lower()
            if stem.startswith(prefix_low):
                small_rels[rel] = full
    # 大样例目录里的非数据文件（比如说明）也算
    big = _pair_up(big_rels) if big_rels else []
    small = _pair_up(small_rels) if small_rels else []
    if big_rels and not big:
        notes.append("大样例目录里没找到 .in/.out 文件")
    for group, kind in ((big, "大样例"), (small, "题目样例")):
        for it in group:
            if it["in"] and not it["out"]:
                notes.append(f"{kind}「{it['label']}」只有输入没有答案")
    return big, small, notes


def detect_bundle(files: dict[str, bytes]) -> dict:
    """识别上传内容的结构，返回 {problems: [...], message: str}。

    支持：
      * 出题工程结构（题目库/<分类号>-<题名>/{题目.md,标程.cpp,data/NN.in|out,大样例/}）
      * 单个题目目录
      * 只有一对 .in/.out 的扁平上传（此时 problems 为空，走老逻辑）
    """
    bundle = MemoryBundle(files)
    found = ip.find_problem_dirs(bundle)
    problems = []
    for prefix, pid, title in found:
        if prefix in ("", "."):
            continue
        prob = ip.parse_problem(bundle, prefix, pid, title, {})
        children = {n[len(prefix) + 1:]: n for n in bundle.names() if n.startswith(prefix + "/")}
        big, small, notes = collect_samples(children, pid)
        prob["big_samples"] = big
        prob["samples"] = small
        prob["sample_notes"] = notes
        problems.append(prob)
    msg = ""
    if len(problems) == 1:
        p = problems[0]
        msg = (f"识别到出题工程结构：{p['prefix']}（{p['pid']} {p['title']}）："
               f"题面{'有' if p['statement'] else '无'}、标程{'有' if p['std'] else '无'}、"
               f"{len(p['cases'])} 组评测数据、{len(p['big_samples'])} 组大样例、"
               f"{len(p['samples'])} 组题目样例")
    elif problems:
        msg = f"识别到 {len(problems)} 道题（{', '.join(p['pid'] for p in problems[:8])}…）"
    return {"problems": problems, "message": msg, "count": len(problems)}


def multipart_samples(files: dict[str, bytes]) -> list[dict]:
    """管理端「大样例」单独上传的那批文件 → 样例分组。

    按同名配对（大样例.in + 大样例.out），没配上的也列出来（能只传一个文件）。
    """
    rels = {name: name for name in files}
    groups = _pair_up(rels)
    if not groups:
        return []
    # 把「其它文件」里的东西并进第一组，避免出现没有数据的空组
    out = []
    for it in groups:
        if it["in"] or it["out"]:
            out.append({**it, "label": it["label"] or "大样例"})
        elif it["extra"]:
            out.append({"stem": "extra", "label": "大样例附件", "in": None, "out": None,
                        "extra": it["extra"]})
    return out


def _samples_root(pid: str, base_dir: str = "") -> str:
    """大样例的存放目录：<base_dir 或 data/samples>/<pid>/。"""
    from . import store
    parent = base_dir or os.path.join(store.DATA_DIR, "samples")
    return os.path.join(parent, pid)


def save_samples(pid: str, groups: list[dict], files: dict[str, bytes],
                 base_dir: str = "") -> dict:
    """把大样例/样例存到 data/samples/<pid>/ 下（学生下载用）。

    groups: detect_bundle 里的大样例+样例分组；files: {相对路径: 内容}
    返回 {"items": [...], "files": N, "bytes": N}
    """
    root = _samples_root(pid, base_dir)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    items, n_files, n_bytes = [], 0, 0
    for kind, group in (("big", groups[0] if groups else []), ("sample", groups[1] if len(groups) > 1 else [])):
        for it in group:
            entry = {"kind": kind, "label": it["label"], "files": []}
            for which in ("in", "out"):
                full = it.get(which)
                if not full:
                    continue
                name = os.path.basename(full)
                # 同名会互相覆盖，加上所属样例名区分（样例1.in / 样例2.in 本来就不重名）
                target = name
                if os.path.exists(os.path.join(root, target)):
                    target = f"{it['label']}-{name}"
                with open(os.path.join(root, target), "wb") as f:
                    f.write(files[full])
                size = len(files[full])
                n_files += 1
                n_bytes += size
                entry["files"].append({"name": target, "size": size, "role": which})
            for full in it.get("extra") or []:
                name = os.path.basename(full)
                with open(os.path.join(root, name), "wb") as f:
                    f.write(files[full])
                n_files += 1
                n_bytes += len(files[full])
                entry["files"].append({"name": name, "size": len(files[full]), "role": "other"})
            items.append(entry)
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "items": items}, f, ensure_ascii=False, indent=2)
    return {"items": items, "files": n_files, "bytes": n_bytes, "dir": root}


def load_samples(pid: str, base_dir: str = "") -> dict:
    """读某个题目已存的大样例/样例清单（学生下载页用）。"""
    root = _samples_root(pid, base_dir)
    path = os.path.join(root, "manifest.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    # 只保留文件还在的条目
    items = []
    for it in data.get("items") or []:
        files = [f for f in (it.get("files") or []) if os.path.isfile(os.path.join(root, f.get("name", "")))]
        if files:
            items.append({**it, "files": files})
    data["items"] = items
    data["dir"] = root
    return data if items else {}


def sample_path(pid: str, name: str, base_dir: str = "") -> str | None:
    """取某个样例文件的绝对路径（防目录穿越）。"""
    root = _samples_root(pid, base_dir)
    safe = os.path.normpath(name).replace("\\", "/").lstrip("/")
    if not safe or safe.startswith("..") or "/" in safe:
        return None
    full = os.path.join(root, safe)
    return full if os.path.isfile(full) else None


def unpack(files: dict[str, bytes]) -> tuple[dict[str, bytes], list[str]]:
    """把上传的东西摊平成 {相对路径: 内容}。zip 会被解开。

    返回 (摊平后的文件, 提示信息)。
    """
    out: dict[str, bytes] = {}
    notes: list[str] = []
    for name, data in files.items():
        clean = name.replace("\\", "/").lstrip("/")
        if clean.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for info in z.infolist():
                        if info.is_dir():
                            continue
                        inner = info.filename.replace("\\", "/").lstrip("/")
                        if "__MACOSX" in inner or inner.startswith("."):
                            continue
                        out[inner] = z.read(info)
                notes.append(f"{clean} 是 zip，已展开")
            except zipfile.BadZipFile:
                notes.append(f"{clean} 不是有效的 zip，已忽略")
            continue
        if clean.startswith(".") or "__MACOSX" in clean:
            continue
        out[clean] = data
    return out, notes


def pair_cases(files: dict[str, bytes]) -> tuple[list[dict], list[str]]:
    """把 {路径: 内容} 配对成测试点。

    返回 (cases, problems)。cases = [{"name": 组名, "in": 路径, "out": 路径或 None}]，
    problems = 没能配对/认不出来的文件说明。
    """
    by_dir: dict[str, dict[str, str]] = {}
    for path in files:
        d = os.path.dirname(path)
        base = os.path.basename(path)
        by_dir.setdefault(d, {})[base] = path

    cases: list[dict] = []
    problems: list[str] = []
    for d in sorted(by_dir, key=_natural_key):
        names = by_dir[d]
        ins = {}
        for base, path in names.items():
            low = base.lower()
            if low.endswith(".in"):
                ins[base[: -len(".in")]] = path
        outs = {}
        for base, path in names.items():
            low = base.lower()
            for ext in (".out", ".ans"):
                if low.endswith(ext):
                    outs[base[: -len(ext)]] = path
                    break
        for stem in sorted(ins, key=_natural_key):
            out_rel = outs.pop(stem, None)
            cases.append({
                "name": (os.path.join(d, stem).replace("\\", "/").strip("/") or stem),
                "in": ins[stem],
                "out": out_rel,
            })
        for stem, path in outs.items():
            problems.append(f"{path}（有答案但没有对应的 .in）")
        for base, path in names.items():
            low = base.lower()
            if low.endswith((".in", ".out", ".ans")):
                continue
            if low.endswith((".cpp", ".c", ".md", ".txt")) or low.endswith((".exe", ".o")):
                continue
            problems.append(f"{path}（不认识的文件，已忽略）")
    cases.sort(key=lambda c: _natural_key(c["name"]))
    return cases, problems


def find_std(files: dict[str, bytes], explicit: str = "") -> tuple[str, str]:
    """找标程：优先用显式上传的，其次按常见文件名猜。返回 (内容, 文件名)。"""
    if explicit:
        for name, data in files.items():
            if os.path.basename(name).lower() == explicit.lower():
                return data.decode("utf-8", "replace"), name
    for name, data in sorted(files.items(), key=lambda kv: _natural_key(kv[0])):
        base = os.path.basename(name).lower()
        if base in [n.lower() for n in ip.STD_NAMES] or base.endswith(".std.cpp"):
            return data.decode("utf-8", "replace"), name
    return "", ""


def take_statement(files: dict[str, bytes], explicit: str = "") -> tuple[str, str]:
    """找题面：优先显式上传的 .md。返回 (内容, 文件名)。"""
    if explicit:
        for name, data in files.items():
            if os.path.basename(name).lower() == explicit.lower():
                return data.decode("utf-8", "replace"), name
    for name, data in sorted(files.items(), key=lambda kv: _natural_key(kv[0])):
        if os.path.basename(name).lower() in [n.lower() for n in ip.STATEMENT_NAMES]:
            return data.decode("utf-8", "replace"), name
    return "", ""


def build_package(dest_root: str, pid: str, title: str, cases: list[dict],
                  files: dict[str, bytes], *, time_ms: int = DEFAULT_TIME_MS,
                  memory_mb: int = DEFAULT_MEMORY_MB, statement: str = "",
                  std_source: str = "", tags: list[str] | None = None,
                  difficulty: int = 3) -> str:
    """把一道题写成 Hydro 题目包，返回包目录。

    时限/内存写在 testdata/config.yaml（Hydro 导入器认这个文件名；
    只写 problem.yaml 的 config 字段对已存在的题目不生效——踩过这个坑）。
    """
    root = os.path.join(dest_root, pid)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "testdata"), exist_ok=True)
    tags = list(tags or ["管理端新建"])
    with open(os.path.join(root, "problem.yaml"), "w", encoding="utf-8") as f:
        f.write(PROBLEM_YAML.format(
            title=title, pid=pid, difficulty=difficulty,
            tags="\n".join(f"  - {t}" for t in tags), ))
    with open(os.path.join(root, "testdata", "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"time: {time_ms}\nmemory: {memory_mb}\n")
    with open(os.path.join(root, "problem_zh.md"), "w", encoding="utf-8") as f:
        f.write(statement or f"# {title}\n\n（还没写题面）\n")
    for i, case in enumerate(cases, 1):
        with open(os.path.join(root, "testdata", f"{i}.in"), "wb") as f:
            f.write(files[case["in"]])
        if case.get("out"):
            with open(os.path.join(root, "testdata", f"{i}.out"), "wb") as f:
                f.write(files[case["out"]])
        else:
            # 没有答案就留空文件，导入后老师可以再补（Hydro 需要成对的 .in/.out）
            open(os.path.join(root, "testdata", f"{i}.out"), "wb").close()
    if std_source:
        os.makedirs(os.path.join(root, "std"), exist_ok=True)
        with open(os.path.join(root, "std", "solution.cpp"), "w", encoding="utf-8") as f:
            f.write(std_source)
    return root


def create_problem(pid: str, title: str, uploads: dict[str, bytes], *,
                   time_ms: int = DEFAULT_TIME_MS, memory_mb: int = DEFAULT_MEMORY_MB,
                   statement: str = "", std_source: str = "", overwrite: bool = False,
                   tags: list[str] | None = None, name: str = "", code: str = "") -> dict:
    """建**一道**题目并导入评测站。返回报告（含数据配对情况与导入结果）。

    uploads 既可以是扁平的一堆 .in/.out，也可以是**出题工程结构**（会自动识别
    题面/标程/数据/大样例）。要一次导入多道题用 create_bundle()。

    name —— **默认英文名**（可留空，如 `candy`）：学生用它建文件夹、命名源文件、
    写 freopen；填了就存进题库（加进比赛时当默认值，本场配题时还能改）。
    只允许小写字母、数字、下划线，不合法会拒绝。
    code —— 老参数名（等价于 `name`），仅为兼容旧调用点保留。

    返回报告里：`number` 是系统分配的**题目编号**（`T00001`，老师侧定位用），
    `name` 是这道题的默认**英文名**，`code` 与 `number` 相同（兼容旧调用点）。
    """
    pid = (pid or "").strip()
    title = (title or "").strip()
    name = (name or code or "").strip()
    files, notes = unpack(uploads)
    problems: list[str] = []

    # 先看是不是出题工程结构：是的话，题目标识/标题/题面/标程/数据/大样例都听它的
    det = detect_bundle(files)
    prob = det["problems"][0] if det["count"] == 1 else None
    sample_info = {}
    if prob:
        cases = [{"name": str(i), "in": c[1], "out": c[2]}
                 for i, c in enumerate(prob["cases"], 1)]
        if not pid:
            pid = prob["pid"]                    # 没填标识就用分类号（题库内部标识）
        if prob["statement"] and not statement:
            statement = prob["statement"]
        if not std_source:
            std_source = ip._decode(files[prob["std"]]) if prob["std"] else ""
        if prob["title"] and (not title or title == pid):
            title = prob["title"]
        notes = [det["message"]] + notes
    else:
        cases, problems = pair_cases(files)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", pid or ""):
        return {"ok": False, "error": "题目标识只能用字母、数字、下划线、点、短横（1~40 个字）；"
                                      "用文件夹上传时会自动取「分类号-题名」里的分类号",
                "notes": notes}
    title = title or pid
    if not cases:
        return {"ok": False,
                "error": "没有配出任何测试点：请上传成对的 .in/.out（或 .in/.ans）文件，"
                         "例如 1.in 和 1.out；也可以直接传一个 zip 或整个出题文件夹。",
                "notes": notes, "problems": problems}

    exists = pid in ip.hydro_problem_pids()
    if exists and not overwrite:
        return {"ok": False,
                "error": f"站点上已经有题目 {pid} 了。要替换请先到「题目列表」把它"
                         f"彻底删除（假删除只是从列表里收起来，标识还占着），再回来建",
                "notes": notes, "cases": len(cases), "exists": True}

    # ---- 题目编号：建题时由系统分配（T00001，跟着题走）；英文名记成默认值
    try:
        number = assign_number(pid, title=title, name=name)
    except CodeError as e:
        return {"ok": False, "error": str(e), "pid": pid, "number": "", "name": "",
                "code": "", "notes": notes, "cases": len(cases)}

    work = os.path.join(ip.HOST_IMPORT_DIR, "_mk")
    os.makedirs(work, exist_ok=True)
    root = build_package(work, pid, title, cases, files, time_ms=time_ms,
                         memory_mb=memory_mb, statement=statement,
                         std_source=std_source, tags=tags)
    if exists:
        ip.hydro_delete_problem(pid)
    ok, err = ip.hydro_import(pid, ip._container_path(ip.HOST_IMPORT_DIR + "/_mk"))
    out = {
        "ok": ok, "error": err, "pid": pid, "title": title,
        "number": number,                       # 题目编号（老师侧）：T00001
        "name": name_of_pid(pid),               # 默认英文名（学生侧）：candy
        "code": number,                         # 兼容旧调用点（= 题目编号）
        "cases": len(cases), "case_names": [c["name"] for c in cases],
        "no_answer": [c["name"] for c in cases if not c.get("out")],
        "notes": notes, "problems": problems,
        "overwritten": bool(exists), "package": root,
        "detected": bool(prob), "sample_notes": (prob or {}).get("sample_notes") or [],
    }
    # 大样例 / 题目样例：存到考试服务这边，学生能在考试页下载（Hydro 那边不加，
    # 免得样例被当成评测数据参与计分）
    if prob and (prob["big_samples"] or prob["samples"]):
        sample_info = save_samples(pid, [prob["big_samples"], prob["samples"]], files)
        out["samples"] = sample_info
    if ok:
        try:      # 导入成功后刷新题库缓存，配题时立刻能搜到
            from . import store
            store.save_catalog(ip_hydro_list())
            out["catalog_refreshed"] = True
        except Exception as e:      # noqa: BLE001
            out["catalog_refreshed"] = False
            out["catalog_error"] = str(e)
    return out


def create_bundle(uploads: dict[str, bytes], *, time_ms: int = DEFAULT_TIME_MS,
                  memory_mb: int = DEFAULT_MEMORY_MB, statement: str = "",
                  overwrite: bool = False, tags: list[str] | None = None,
                  only: list[str] | None = None,
                  names: dict[str, str] | None = None,
                  codes: dict[str, str] | None = None) -> dict:
    """一次导入出题工程文件夹里的**多道题**。返回 {"ok", "items": [每道题的报告], "message"}。

    names —— 可选的 {题库标识: 默认英文名}：想给某道题定个默认英文名的写在这里，
             其余留空（加进比赛时可以再填）。每道题的报告里都有系统分配的
             **题目编号** `number`（T00001）与默认英文名 `name`。
    codes —— `names` 的旧参数名（等价），仅为兼容旧调用点保留。
    """
    files, notes = unpack(uploads)
    det = detect_bundle(files)
    if not det["count"]:
        return {"ok": False, "error": "没识别出题目：请上传出题工程的文件夹（里面有题目.md、"
                                      "data、标程.cpp），或者成对的 .in/.out 文件。",
                "notes": notes}
    want_names = dict(codes or {})
    want_names.update(names or {})
    items = []
    for prob in det["problems"]:
        if only and prob["pid"] not in only:
            continue
        items.append(create_problem(prob["pid"], prob["title"], files,
                                    time_ms=time_ms, memory_mb=memory_mb,
                                    statement=statement, overwrite=overwrite, tags=tags,
                                    name=want_names.get(prob["pid"], "")))
    ok_all = all(i.get("ok") for i in items) if items else False
    msg = (f"共识别并导入 {len(items)} 道题："
           + "、".join(f"{i['pid']}→{i.get('number') or '没编号'}"
                       f"（英文名 {i.get('name') or '待填'}）"
                       f"({'成功' if i.get('ok') else '失败'})" for i in items))
    return {"ok": ok_all, "items": items, "message": msg, "detected": det["message"]}


def ip_hydro_list() -> list[dict]:
    """重新读一遍站点题库（给缓存刷新用）。"""
    from . import hydro_client
    return hydro_client.list_problems()


if __name__ == "__main__":      # 手工调试用
    import sys
    print("这是给管理端用的模块；命令行请用 import_problemset.py")
    raise SystemExit(0 if len(sys.argv) == 1 else 2)
