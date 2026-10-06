"""CSP 训练站 —— 数据存储层（多比赛版）。

目录结构：

    data/
      groups.json                # 名单分组：[{gid, name, students:[姓名...], at}]
      contests.json              # 比赛索引：[{id, title, rule, open, released, created_at}]
      contests/<比赛id>/
        exam.json                # 本场题目：[{no, pid, title, slug, full}]
        roster.json              # 本场名单：{考号: {name, uname, pw, uid, at}}
        results.json             # 本场成绩：{考号: {problems, total, ...}}
        uploads/<考号>/          # 收到的代码（CSP 是文件夹结构，OI/IOI 是每题一个文件）

考号是**按场次随机生成**的，格式 `<前缀>-<级别><5 位随机数>`，例如 `GD-S48213`：

* 数字部分是**纯随机数**（00001~99999），不是从 1 开始的排列 —— 与学生姓名、名单顺序、
  机位、上一场都无关，学生猜不到别人的号；
* 级别字母 J / S 对应入门级 / 提高级，同时决定比赛默认时长；
* 同一场里不重号；不同场次各发各的。

姓名与考号的对应关系存在**每场的 roster.json** 里，而不是全局（老版本的
students.json 全局考号池只用于迁移，见 `_migrate_roster`）。

三种赛制（rule）——**提交方式统一**：学生交以考号命名的**文件夹**（上传），
赛制只决定程序怎么写、怎么计分：

    OI   —— 国内 OI/NOIP 风格：程序用标准输入输出，按**最后一次**提交计分
    IOI  —— 国际信息学奥赛风格：标准输入输出，每题取**多次提交的最高分**
    CSP  —— CSP-J/S 复赛风格：按题目英文名分子文件夹，代码必须 freopen
            读写 <英文名>.in/.out

**成绩可见性对所有赛制一律相同**：判分照常跑（老师要看成绩），但学生端在老师点
「公布成绩」之前拿不到任何判定与分数（连"部分正确"都不给），公布后才能在「查成绩」页看到。
"""

from __future__ import annotations

import json
import os
import random
import re
import secrets
import threading
import time

from ..config import DATA_DIR          # 数据目录由 config 统一指定（部署根/data）
_LOCK = threading.RLock()

DEFAULT_PREFIX = "GD"     # 考号前缀（省份代码风格）
DEFAULT_WIDTH = 5         # 随机数位数：GD-S48213
DEFAULT_LEVEL = "S"       # 默认级别：S 提高级 / J 入门级
_RNG = random.SystemRandom()      # 考号随机生成用（不可预测，避免学生猜别人考号）

#: 级别 → 默认比赛时长（分钟）。J 组 3.5 小时、S 组 4 小时，与真实认证一致。
LEVEL_MINUTES = {"J": 210, "S": 240}
LEVEL_NAMES = {"J": "入门级 J 组", "S": "提高级 S 组"}

#: 赛制定义（界面展示与判分逻辑共用）
#:
#: **没有「反馈模式」这个字段**：所有赛制一律「赛中零反馈」—— 判分照常跑（老师要看成绩），
#: 但学生端在老师「公布成绩」之前拿不到任何判定与分数，连"部分正确"都不给。
#: 以前这里还有 `feedback`（none/live）和本场 `feedback_mode`（full/train）两层开关，
#: 已按需求删掉：只有一种行为，就不用两个概念去描述它。
RULES = {
    "OI": {
        "key": "OI", "name": "OI 赛制",
        "best_of": False, "freopen": False,
        "desc": "国内 OI/NOIP 风格：程序用**标准输入输出**（cin/cout，不需要 freopen），"
                "成绩按**最后一次**提交计分，老师在管理端点「发布成绩」后学生才能看到分数。",
    },
    "IOI": {
        "key": "IOI", "name": "IOI 赛制",
        "best_of": True, "freopen": False,
        "desc": "国际信息学奥赛风格：程序用**标准输入输出**，"
                "每题取多次提交的**最高分**；成绩同样等老师公布后才可见。",
    },
    "CSP": {
        "key": "CSP", "name": "CSP 赛制",
        "best_of": False, "freopen": True,
        "desc": "CSP-J/S 复赛风格：代码必须用 freopen 读写 `<题目英文名>.in` / `<题目英文名>.out`；"
                "文件夹名或 freopen 文件名写错，这题就是 0 分。赛后老师公布成绩。",
    },
}
DEFAULT_RULE = "CSP"

#: 三种赛制的**提交方式是一样的**：学生交以考号命名的文件夹（上传文件）。
#: 赛制只影响：程序怎么写（要不要 freopen）、怎么计分。
SUBMIT_UPLOAD_NOTE = "交以考号命名的文件夹（上传文件）"


def rule_of(contest: dict) -> dict:
    return RULES.get((contest or {}).get("rule") or DEFAULT_RULE, RULES[DEFAULT_RULE])


def level_of(contest: dict) -> str:
    """比赛的级别：`J` 入门级 / `S` 提高级。老比赛缺字段时按 S 兜底。"""
    lv = str((contest or {}).get("level") or "").strip().upper()
    return lv if lv in LEVEL_MINUTES else DEFAULT_LEVEL


def level_name(level: str) -> str:
    return LEVEL_NAMES.get((level or "").upper(), LEVEL_NAMES[DEFAULT_LEVEL])


def duration_of(contest: dict) -> int:
    """比赛时长（分钟）。没设过就按级别取默认（J 210 / S 240）。"""
    if contest and contest.get("duration_min"):
        try:
            v = int(contest["duration_min"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return LEVEL_MINUTES[level_of(contest)]


# ------------------------------------------------------------------ 基础读写


def _path(*parts: str) -> str:
    p = os.path.join(DATA_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _load(path: str, default):
    if not os.path.exists(path):
        return default() if callable(default) else default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default() if callable(default) else default


def _save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ------------------------------------------------------------------ 学生池（老数据迁移用）

#: 老版本的全局考号池：考号 -> {name, pw, uid}。现在考号按场次分配，
#: 这里只在新旧数据迁移时读一次，不再写入。


def _students_path() -> str:
    return _path("students.json")


def load_students() -> dict:
    """老版本的全局学生池（只用于迁移旧数据；新数据不要用它）。"""
    return _load(_students_path(), dict)


def make_kaohao(num: int, prefix: str = DEFAULT_PREFIX, level: str = DEFAULT_LEVEL,
                width: int = DEFAULT_WIDTH) -> str:
    """拼一个考号：<前缀>-<级别><随机数>，如 GD-S48213、GD-J07915。"""
    lv = (level or DEFAULT_LEVEL).upper()
    if lv not in LEVEL_MINUTES:
        lv = DEFAULT_LEVEL
    return f"{prefix}-{lv}{num:0{width}d}"


def split_kaohao(kaohao: str) -> tuple[str, str, str]:
    """拆考号 → (前缀, 级别, 数字串)。拆不出来就返回 ("", "", "")。"""
    import re
    m = re.match(r"^([A-Za-z]+)-([JS])(\d+)$", str(kaohao or ""))
    if not m:
        return "", "", ""
    return m.group(1), m.group(2), m.group(3)


def new_account(cid: str, kaohao: str) -> dict:
    """给某个考号准备一个评测站账号（每场考试的考号不同，账号名也带上场次）。"""
    return {
        "pw": secrets.token_hex(6),  # 避免以 "-" 开头被 hydrooj cli 当成命令行开关
        "uname": f"{cid}-{kaohao}",
        "uid": None,
    }


# ------------------------------------------------------------------ 名单分组


def _groups_path() -> str:
    return _path("groups.json")


def load_groups() -> list[dict]:
    """名单分组：[{gid, name, students: [姓名...], at, last_used}]。"""
    data = _load(_groups_path(), dict)
    items = data.get("items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def save_groups(items: list[dict]) -> None:
    with _LOCK:
        _save(_groups_path(), {"items": items})


def get_group(gid: str) -> dict | None:
    for g in load_groups():
        if g.get("gid") == gid:
            return g
    return None


def clean_names(names: list[str]) -> tuple[list[str], list[str]]:
    """整理姓名列表：去空白、去重（保序）。返回 (整理后, 重复的)。"""
    out, seen, dup = [], set(), []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        if name in seen:
            dup.append(name)
            continue
        seen.add(name)
        out.append(name)
    return out, dup


def create_group(name: str, names: list[str]) -> dict:
    with _LOCK:
        items = load_groups()
        cleaned, _ = clean_names(names)
        seq = len(items) + 1
        gid = "g" + str(seq)
        while any(g.get("gid") == gid for g in items):
            seq += 1
            gid = "g" + str(seq)
        g = {"gid": gid, "name": name.strip() or f"分组 {seq}",
             "students": cleaned, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        items.append(g)
        save_groups(items)
        return g


def update_group(gid: str, *, name: str | None = None, names: list[str] | None = None) -> dict | None:
    with _LOCK:
        items = load_groups()
        for g in items:
            if g.get("gid") != gid:
                continue
            if name is not None:
                g["name"] = name.strip() or g["name"]
            if names is not None:
                cleaned, _ = clean_names(names)
                g["students"] = cleaned
            g["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_groups(items)
            return g
    return None


def delete_group(gid: str) -> bool:
    with _LOCK:
        items = load_groups()
        keep = [g for g in items if g.get("gid") != gid]
        if len(keep) == len(items):
            return False
        save_groups(keep)
        return True


# ------------------------------------------------------------------ 比赛


def _index_path() -> str:
    return _path("contests.json")


def _cp(cid: str, name: str) -> str:
    return _path("contests", cid, name)


def _migrate_if_needed() -> None:
    """把旧版单场比赛的数据平滑迁移成 c1 号比赛。"""
    legacy_exam = _path("exam.json")
    legacy_roster = _path("roster.json")
    legacy_results = _path("results.json")
    if os.path.exists(_index_path()) or not os.path.exists(legacy_exam):
        return
    with _LOCK:
        exam = _load(legacy_exam, dict) or {}
        roster = _load(legacy_roster, dict) or {}
        results = _load(legacy_results, dict) or {}
        # 老的 roster.json 就是全局学生池，搬到 students.json
        if roster and not os.path.exists(_students_path()):
            _save(_students_path(), roster)
        _save(_index_path(), [{
            "id": "c1", "title": exam.get("title") or "CSP 模拟赛",
            "rule": DEFAULT_RULE, "open": exam.get("open", True),
            "released": exam.get("released", False),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }])
        _save(_cp("c1", "exam.json"), exam)
        _save(_cp("c1", "roster.json"), sorted(roster.keys()))
        for entry in results.values():          # 老数据的 total 可能是错的，按题重算
            if isinstance(entry, dict):
                entry["total"] = entry_total(entry)
        _save(_cp("c1", "results.json"), results)
        # 旧的 uploads 目录也搬过去
        old_uploads = os.path.join(DATA_DIR, "uploads")
        new_uploads = os.path.join(DATA_DIR, "contests", "c1", "uploads")
        if os.path.isdir(old_uploads) and not os.path.isdir(new_uploads):
            try:
                os.rename(old_uploads, new_uploads)
            except OSError:
                pass


def list_contests() -> list[dict]:
    _migrate_if_needed()
    items = _load(_index_path(), list)
    return items if isinstance(items, list) else []


def save_contests(items: list[dict]) -> None:
    with _LOCK:
        _save(_index_path(), items)


def get_contest(cid: str) -> dict | None:
    for c in list_contests():
        if c.get("id") == cid:
            return c
    return None


def default_contest_id() -> str:
    items = list_contests()
    return items[0]["id"] if items else ""


def create_contest(title: str, rule: str = DEFAULT_RULE, prefix: str = DEFAULT_PREFIX,
                   level: str = DEFAULT_LEVEL, duration_min: int | None = None) -> dict:
    """新建一场比赛。

    level         —— 级别 `J` 入门级 / `S` 提高级，决定考号字母与默认时长
    duration_min  —— 比赛时长（分钟），不填按级别取默认（J 210 / S 240）

    没有「反馈模式」参数：所有比赛一律赛中零反馈，老师点「公布成绩」后才对学生可见。
    """
    with _LOCK:
        items = list_contests()
        n = 1
        while any(c.get("id") == f"c{n}" for c in items):
            n += 1
        lv = (level or DEFAULT_LEVEL).upper()
        if lv not in LEVEL_MINUTES:
            lv = DEFAULT_LEVEL
        contest = {
            "id": f"c{n}",
            "title": title.strip() or f"比赛 {n}",
            "rule": rule if rule in RULES else DEFAULT_RULE,
            "open": True, "released": False, "prefix": prefix,
            "level": lv,
            "duration_min": int(duration_min or LEVEL_MINUTES[lv]),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        items.append(contest)
        save_contests(items)
        _save(_cp(contest["id"], "exam.json"), {"title": contest["title"], "notice": "",
                                                "open": True, "released": False, "problems": []})
        _save(_cp(contest["id"], "roster.json"), [])
        _save(_cp(contest["id"], "results.json"), {})
        return contest


def update_contest(cid: str, **fields) -> dict | None:
    with _LOCK:
        items = list_contests()
        for c in items:
            if c.get("id") == cid:
                if "rule" in fields and fields["rule"] != c.get("rule") and load_results(cid):
                    exam = dict(load_exam(cid), released=False)
                    _save(_cp(cid, "exam.json"), exam)
                    fields["released"] = False
                c.update(fields)
                save_contests(items)
                return c
    return None


def delete_contest(cid: str) -> bool:
    """删除比赛（连同名册、成绩、提交的代码）。"""
    import shutil

    with _LOCK:
        items = list_contests()
        keep = [c for c in items if c.get("id") != cid]
        if len(keep) == len(items):
            return False
        save_contests(keep)
        shutil.rmtree(os.path.join(DATA_DIR, "contests", cid), ignore_errors=True)
        return True


# ------------------------------------------------------------------ 单场比赛的内容


def load_exam(cid: str) -> dict:
    exam = _load(_cp(cid, "exam.json"), dict) or {}
    out = {"title": "", "notice": "", "open": True, "released": False, "problems": []}
    out.update(exam)
    return out


def save_exam(cid: str, exam: dict) -> None:
    with _LOCK:
        old = load_exam(cid)
        if old.get("problems", []) != exam.get("problems", []) and load_results(cid):
            exam = dict(exam, released=False)
            update_contest(cid, released=False)
        _save(_cp(cid, "exam.json"), exam)


def _migrate_roster(cid: str, data) -> dict[str, dict]:
    """老版 roster.json 是一个考号列表（姓名在全局学生池里），这里归一化。

    同样保留老的评测站账号名（就是考号本身），否则历史提交会另起账号。
    """
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if isinstance(v, dict):
                out[str(k)] = v
            else:
                out[str(k)] = {"name": str(v)}
        return out
    if not isinstance(data, list):
        return {}
    students = load_students()
    out = {}
    for k in data:
        k = str(k)
        old = students.get(k) or {}
        out[k] = {
            "name": old.get("name", "?"),
            "pw": old.get("pw") or secrets.token_urlsafe(6),
            "uname": k,                   # 老账号名就是考号
            "uid": old.get("uid"),
            "at": old.get("at", ""),
        }
    return out


def load_roster(cid: str) -> dict[str, dict]:
    """本场名单：{考号: {name, uname, pw, uid, at}}。

    老格式（考号列表 + 全局学生池）会在这里升级，并且**升级结果立刻写回**——
    踩过：只做内存转换的话，每次读取都要靠 students.json 补姓名/密码，
    那个文件一旦被挪走/删掉，姓名就变成 "?"、密码还会每次随机生成（学生直接登录不上）。
    """
    path = _cp(cid, "roster.json")
    data = _load(path, dict)
    roster = _migrate_roster(cid, data)
    if isinstance(data, list):
        try:
            _save(path, {k: roster[k] for k in sorted(roster)})
        except OSError:
            pass
    return roster


def save_roster(cid: str, mapping: dict[str, dict]) -> None:
    with _LOCK:
        _save(_cp(cid, "roster.json"), {k: mapping[k] for k in sorted(mapping)})


def roster_kaohaos(cid: str) -> list[str]:
    """本场考号列表（按考号排序）。"""
    return sorted(load_roster(cid))


def kaohao_of_name(cid: str, name: str) -> str:
    for k, v in load_roster(cid).items():
        if v.get("name") == name:
            return k
    return ""


def student_name(cid: str, kaohao: str) -> str:
    """某场考试里某考号对应的姓名。"""
    return (load_roster(cid).get(kaohao) or {}).get("name", "?")


def names_in_roster(cid: str) -> list[str]:
    return [v.get("name", "?") for v in load_roster(cid).values()]


def _random_numbers(count: int, taken: set[int], width: int = DEFAULT_WIDTH) -> list[int]:
    """取 count 个还没被占用的**纯随机**序号（00001~99999）。

    不能用「1..N 打乱分配」：那样号码本身是连续的（出现 1~8 这种小号），
    学生能推断出考场总人数、也能大概猜到别人手里是什么号。
    纯随机之后号码与姓名、名单顺序、机位、上一场全都无关。
    """
    hi = 10 ** width - 1
    if count > hi:
        raise ValueError(f"人数（{count}）超过考号容量（{hi}），请加长位宽")
    nums: list[int] = []
    while len(nums) < count:
        n = _RNG.randint(1, hi)
        if n in taken:
            continue                      # 撞号就重取
        taken.add(n)
        nums.append(n)
    return nums


#: 名单条目多久算「陈年」，可以被新的随机号覆盖（天）
STALE_DAYS = 15


def _entry_age_days(entry: dict) -> float:
    """名单条目距今多少天（读 `at` 时间戳；读不出来就当作很新，不去覆盖别人的号）。"""
    at = str((entry or {}).get("at") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            t = time.strptime(at, fmt)
            return (time.time() - time.mktime(t)) / 86400.0
        except ValueError:
            continue
    return 0.0


def _taken_numbers(roster: dict, *, stale_days: float = STALE_DAYS) -> set[int]:
    """本场已被占用的号码。

    **超过 `stale_days` 天没人动的号不算占用** —— 那是很久以前的残留
    （比如删了比赛又用同一个 id 重建），新学生随机到时可以直接覆盖它；
    比较新的号则是「有人正在用」，随机到就重取（见 `_random_numbers`）。
    老格式的号（`GD-0001`）也一并算进来，避免撞车。
    """
    taken = set()
    for k, v in roster.items():
        _, _, digits = split_kaohao(k)
        if not digits.isdigit():
            continue
        if stale_days > 0 and _entry_age_days(v) > stale_days:
            continue                      # 陈年残留：可以让新的随机号覆盖
        taken.add(int(digits))
    return taken


def assign_kaohaos(cid: str, names: list[str], prefix: str | None = None,
                   level: str | None = None, width: int = DEFAULT_WIDTH) -> dict[str, dict]:
    """把姓名加进本场名单并**生成考号**（纯随机）。

    - 已经在名单里的人保持不变（不会因为再导入一次就换考号）；
    - 新来的拿**纯随机**的 5 位号，与名单顺序无关；
    - 同一场考试里考号互不相同；
    - **随机到的号如果和别人撞了**：对方是**半个月前**的陈年残留就直接覆盖，
      是最近还在用的就重新随机（见 `_taken_numbers`）；
    - 前缀与级别默认取本场比赛的设置。
    """
    with _LOCK:
        contest = get_contest(cid) or {}
        prefix = prefix or contest.get("prefix") or DEFAULT_PREFIX
        level = level or level_of(contest)
        roster = load_roster(cid)
        cleaned, _ = clean_names(names)
        have = {v.get("name") for v in roster.values()}
        fresh = [n for n in cleaned if n not in have]
        if fresh:
            taken = _taken_numbers(roster)
            nums = _random_numbers(len(fresh), taken, width)
            for name, num in zip(fresh, nums):
                kaohao = make_kaohao(num, prefix, level, width)
                # 撞到「最近有人用」的号 → 重新随机；撞到半个月前的残留 → 覆盖它
                while kaohao in roster and _entry_age_days(roster[kaohao]) <= STALE_DAYS:
                    nums2 = _random_numbers(1, taken, width)
                    kaohao = make_kaohao(nums2[0], prefix, level, width)
                roster[kaohao] = dict(new_account(cid, kaohao), name=name,
                                      at=time.strftime("%Y-%m-%d %H:%M:%S"))
        save_roster(cid, roster)
        return roster


def replace_roster(cid: str, names: list[str], prefix: str | None = None,
                   level: str | None = None, width: int = DEFAULT_WIDTH) -> dict[str, dict]:
    """用这批姓名**重建**本场名单，并重新生成考号（整场重排）。"""
    with _LOCK:
        if load_results(cid):
            raise ValueError("本场已有提交，不能重建名单或重排考号；请新建比赛。")
        contest = get_contest(cid) or {}
        prefix = prefix or contest.get("prefix") or DEFAULT_PREFIX
        level = level or level_of(contest)
        cleaned, _ = clean_names(names)
        nums = _random_numbers(len(cleaned), set(), width)
        roster = {}
        for name, num in zip(cleaned, nums):
            kaohao = make_kaohao(num, prefix, level, width)
            roster[kaohao] = dict(new_account(cid, kaohao), name=name,
                                  at=time.strftime("%Y-%m-%d %H:%M:%S"))
        save_roster(cid, roster)
        return roster


def reshuffle_kaohaos(cid: str, prefix: str | None = None,
                      level: str | None = None, width: int = DEFAULT_WIDTH) -> dict[str, dict]:
    """把本场已有的考号重新随机生成（学生不变）。"""
    names = [v.get("name", "?") for _, v in sorted(load_roster(cid).items())]
    return replace_roster(cid, names, prefix, level, width)


def remove_from_roster(cid: str, kaohaos: list[str]) -> dict[str, dict]:
    with _LOCK:
        roster = load_roster(cid)
        for k in kaohaos:
            roster.pop(k, None)
        save_roster(cid, roster)
        return roster


def roster_detail(cid: str) -> list[dict]:
    """本场名单（含姓名、评测站账号密码、提交状态），按考号排序。"""
    results = load_results(cid)
    out = []
    for k, v in sorted(load_roster(cid).items()):
        out.append({
            "kaohao": k, "name": v.get("name", "?"), "pw": v.get("pw", ""),
            "uname": v.get("uname", k), "uid": v.get("uid"),
            "submitted": k in results,
        })
    return out


def find_contests_of_kaohao(kaohao: str) -> list[dict]:
    """这个考号出现在哪些比赛里（学生登录时用来定位考场）。"""
    out = []
    for c in list_contests():
        if kaohao in load_roster(c["id"]):
            out.append(c)
    return out


def load_results(cid: str) -> dict:
    return _load(_cp(cid, "results.json"), dict)


def save_results(cid: str, results: dict) -> None:
    with _LOCK:
        _save(_cp(cid, "results.json"), results)


def put_result(cid: str, kaohao: str, payload: dict) -> None:
    with _LOCK:
        results = load_results(cid)
        results[kaohao] = payload
        save_results(cid, results)


def upload_dir(cid: str, kaohao: str) -> str:
    d = _path("contests", cid, "uploads", kaohao)
    return d


# ------------------------------------------------------------------ 题库清单缓存

def _catalog_path() -> str:
    return _path("problem_catalog.json")


def load_catalog() -> dict:
    """站点题库清单的本地缓存（配题时的下拉搜索用，避免每次都查数据库）。"""
    return _load(_catalog_path(), dict) or {}


def save_catalog(items: list[dict]) -> None:
    _save(_catalog_path(), {
        "items": items,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })


def entry_total(entry: dict, cid: str = "") -> int:
    """按题目求和；指定比赛时只计本场题目，存储 total 不作为权威。"""
    parts = entry.get("problems") or {}
    values = [parts.get(problem_dir_name(int(p["no"]))) for p in load_exam(cid).get("problems", [])] if cid else parts.values()
    return sum(int((v or {}).get("score") or 0) for v in values)


def result_pending(entry: dict) -> bool:
    return bool(entry.get("judging") or entry.get("rejudge_error") or
                entry.get("submission_state") in ("queued", "judging", "error") or
                any(p.get("pending") for p in entry.get("problems", {}).values()))


def ranking(cid: str, include_all: bool = False, *, _results: dict | None = None) -> list[dict]:
    """统一成绩投影：只计本场题目，名单外记录不参赛，未完成成绩不参与排名。"""
    with _LOCK:
        results, roster = load_results(cid) if _results is None else _results, load_roster(cid)
        keys = [problem_dir_name(int(p["no"])) for p in load_exam(cid).get("problems", [])]
        rows = []
        for kaohao, student in roster.items():
            entry = results.get(kaohao) or {}
            if not include_all and kaohao not in results:
                continue
            parts = entry.get("problems", {})
            rows.append({"kaohao": kaohao, "name": student.get("name", "?"),
                         "total": sum(int((parts.get(k) or {}).get("score") or 0) for k in keys),
                         "problems": parts, "structure_ok": entry.get("structure_ok", True),
                         "at": entry.get("submitted_at", ""), "submitted": kaohao in results,
                         "pending": result_pending(entry), "rank": None})
        rows.sort(key=lambda r: (2 if r["pending"] else 1 if not r["submitted"] else 0, -r["total"], r["name"], r["kaohao"]))
        rank, last = 0, None
        for i, row in enumerate(rows, 1):
            if row["pending"] or not row["submitted"]:
                continue
            if row["total"] != last:
                rank, last = i, row["total"]
            row["rank"] = rank
        return rows


def set_released(cid: str, released: bool) -> None:
    """校验与公布在同一把锁中完成，避免与上传或重测竞态。"""
    with _LOCK:
        contest = get_contest(cid)
        if not contest:
            raise ValueError("比赛不存在")
        exam = load_exam(cid)
        results = load_results(cid).values()
        if released and any(result_pending(r) for r in results):
            raise ValueError("仍有提交未评测完成或需要重测，暂不能公布成绩。")
        if released and any((r.get("submission_exam") is not None and
                r["submission_exam"].get("problems", []) != exam.get("problems", [])) or
                (r.get("judge_rule") and r["judge_rule"].get("key") != rule_of(contest)["key"])
                for r in results):
            raise ValueError("成绩对应的题目或赛制已改变，请重测后再公布。")
        exam["released"] = bool(released)
        save_exam(cid, exam)
        update_contest(cid, released=bool(released))


def invalidate_problem_results(pid: str, reason: str) -> None:
    """题目数据或限额变更后撤回相关成绩；源码、历史分数与备份保留供重测。"""
    with _LOCK:
        for contest in list_contests():
            cid = contest["id"]
            if not any(p.get("pid") == pid for p in load_exam(cid).get("problems", [])):
                continue
            results = load_results(cid)
            if not results:
                continue
            for entry in results.values():
                entry.update(rejudge_error=reason, submission_state="error")
            _save(_path("contests", cid, "results.json"), results)
            set_released(cid, False)


# ------------------------------------------------------------------ 题目与文件名


def problem_dir_name(no: int) -> str:
    """内部使用的题目键（结果表用它做列名）。"""
    return f"T{no}"


#: 题目编号的格式：`T` + 5 位数字（如 T00001）。**建题时系统分配，老师不能改**——
#: 它只是老师嘴里的「T00001 那道题」，学生看不到它。
_PROBLEM_NUMBER_RE = re.compile(r"^[Tt]\d{5}$")


def is_problem_number(value) -> bool:
    """是不是系统分配的题目编号（`T00001`）。"""
    return bool(_PROBLEM_NUMBER_RE.match(str(value or "").strip()))


def number_of(prob: dict) -> str:
    """题目的**题目编号**（老师侧）：`T` + 5 位数字，如 `T00001`。

    建题时由系统分配、跟着题走（登记在 `data/problem_codes.json`），老师不能改，
    只用来定位查找（「T00001 那道题」）。学生侧看到的是 `code_of()` 的英文名。
    取值顺序：`number` → `code`（本模型的 `code` 就是编号）→ `""`（题库里还没登记）。
    """
    prob = prob or {}
    for key in ("number", "code"):
        v = str(prob.get(key) or "").strip().upper()
        if is_problem_number(v):
            return v
    return ""


def code_of(prob: dict) -> str:
    """题目的**英文名**（学生侧）—— 学生用它建文件夹、命名源文件、写 freopen。

    老师在「新建 / 管理考试」时设置（本场 `exam.json` 的 `name` 字段）；
    同一道题加进哪场比赛、排第几个都不变。取值顺序：
    `name`（本场填的英文名）→ `code`（老数据里存的就是英文名）→
    `slug`（更老的字段）→ `p{题号}`（最后兜底）。

    两处刻意为之：

    * **跳过看起来是题目编号的值**（`T00001`）—— 本模型的 `code` 有时存的是老师侧的
      题目编号，那种值绝不能当学生看到的名字，否则学生就看到编号了。
    * 兜底用 `p{题号}` 而**不是** `T{题号}`：`T1` 长得就像题目编号，学生会当成编号；
      `p1` 一看就是"还没起英文名"的占位（老师配题时应当填上）。
    """
    prob = prob or {}
    for key in ("name", "code", "slug"):
        v = str(prob.get(key) or "").strip()
        if not v:
            continue
        if key != "name" and is_problem_number(v):
            continue                      # `code`/`slug` 里是题目编号 → 不是英文名
        return v
    return f"p{int(prob.get('no', 1))}"


def slug_of(prob: dict) -> str:
    """兼容旧名（历史代码到处在用）：等价于 `code_of`（英文名）。"""
    return code_of(prob)


def valid_code(code: str) -> bool:
    """英文名是否合法：只能用小写字母、数字、下划线（要当文件夹名和文件名）。"""
    return bool(re.match(r"^[a-z0-9_]{1,32}$", str(code or "")))


def valid_name(name: str) -> bool:
    """兼容新叫法：英文名的合法性检查（等价于 `valid_code`）。"""
    return valid_code(name)


def file_names_of(prob: dict) -> tuple[str, str]:
    base = slug_of(prob)
    return f"{base}.in", f"{base}.out"


# ------------------------------------------------------------------ 管理密钥


def admin_key() -> str:
    """教师控制台的访问密钥，首次运行时生成并保存。"""
    p = _path("admin_key.txt")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    k = secrets.token_urlsafe(16)
    with open(p, "w", encoding="utf-8") as f:
        f.write(k)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return k
