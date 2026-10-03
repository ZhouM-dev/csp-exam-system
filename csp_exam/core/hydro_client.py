"""CSP 模拟赛考试服务 —— 与 Hydro 对接。

做的事情：
  * 给每个考号建一个 Hydro 账号（学生不用注册，账号由教师端批量生成）
  * 以该账号身份提交代码到对应题目
  * 从数据库读回判分结果（状态、得分、时间、内存，
    以及逐点明细：判定、用时、内存、输入、学生输出、标准答案）

只用标准库：HTTP 用 urllib，读判定用 docker exec 里的 mongosh（避免额外装 pymongo）。
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import HYDRO_BASE, HYDRO_COMPOSE_FILE, HYDRO_DIR

COMPOSE_DIR = HYDRO_DIR
COMPOSE = ["docker", "compose", "-f", HYDRO_COMPOSE_FILE]

#: Hydro 的语言标识（评测机 langs.yaml 里的键）
LANG_BY_EXT = {
    ".cpp": "cc.cc14o2",
    ".cc": "cc.cc14o2",
    ".cxx": "cc.cc14o2",
    ".c++": "cc.cc14o2",
    ".c": "c.c11o2",
    ".pas": "pas.pas",
}

#: Hydro 的评测状态码（取自 @hydrooj/common/status.ts，别看错了）
STATUS = {
    0: "等待评测", 1: "答案正确", 2: "答案错误", 3: "运行超时", 4: "内存超限",
    5: "输出超限", 6: "运行错误", 7: "编译错误", 8: "系统错误", 9: "已取消",
    10: "未知错误", 11: "被 hack", 20: "正在评测", 21: "正在编译", 22: "已取数据",
    30: "已忽略", 31: "格式错误", 32: "Hack 成功", 33: "Hack 失败",
}
STATUS_AC = 1
#: 这些状态表示还没评完，要继续等（0=排队 20=评测中 21=编译中 22=取数据）
STATUS_PENDING = (0, 20, 21, 22)

_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, tuple[urllib.request.OpenerDirector, float]] = {}   # uname -> (opener, 时间)


class HydroError(Exception):
    pass


# ------------------------------------------------------------------ 底层

def new_opener() -> urllib.request.OpenerDirector:
    """带 cookie 罐的 opener。

    必须用 cookie 罐：Hydro 登录后返回 302 跳转，urllib 会自动跟随，
    跳转响应里的 Set-Cookie 就丢了（手工用 curl -c 才拿得到）。
    """
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _request(opener: urllib.request.OpenerDirector, path: str, data: dict | None = None,
             timeout: int = 30) -> tuple[int, str]:
    """返回 (状态码, 响应体)。cookie 由 opener 自动管理。"""
    url = HYDRO_BASE + path
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body)
    req.add_header("User-Agent", "csp-exam/1.0")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HydroError(f"无法连接评测系统：{e}") from e


def _mongosh(js: str, timeout: int = 60) -> str:
    """在 mongo 容器里跑一段 JS，返回标准输出。"""
    cmd = COMPOSE + ["exec", "-T", "oj-mongo", "mongosh", "hydro", "--quiet", "--eval", js]
    try:
        p = subprocess.run(cmd, cwd=COMPOSE_DIR, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise HydroError(f"查询评测数据库失败：{e}") from e
    out = (p.stdout or "").strip()
    if p.returncode != 0 and not out:
        raise HydroError(f"查询评测数据库失败：{(p.stderr or '').strip()[:300]}")
    return out


def _mongosh_json(js: str, timeout: int = 60):
    """跑 JS 并解析 EJSON 结果。"""
    raw = _mongosh(f"EJSON.stringify({js})", timeout=timeout)
    line = raw.splitlines()[-1].strip() if raw else ""
    if not line or line in ("undefined", "null"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        raise HydroError(f"解析评测结果失败：{e}：{line[:200]}") from e


# ------------------------------------------------------------------ 账号

def next_uid() -> int:
    data = _mongosh_json("db.user.find({},{_id:1}).sort({_id:-1}).limit(1).toArray()")
    if not data:
        return 3
    return int(data[0]["_id"]) + 1


def uid_of(uname: str) -> int | None:
    """按用户名查 uid（比启动 Hydro 进程便宜得多）。"""
    data = _mongosh_json(f'db.user.find({{uname:"{uname}"}},{{_id:1}}).toArray()')
    if not data:
        return None
    try:
        return int(data[0]["_id"])
    except (KeyError, TypeError, ValueError):
        return None


def user_exists(uname: str) -> bool:
    return uid_of(uname) is not None


def set_password(uname: str, password: str) -> bool:
    """重置密码。注意 CLI 必须用 uid（传用户名会静默失败——踩过）。"""
    uid = uid_of(uname)
    if uid is None:
        return False
    cmd = COMPOSE + ["exec", "-T", "oj-backend", "hydrooj", "cli",
                     "user", "setPassword", str(uid), password]
    with _CREATE_LOCK:
        _throttle()
        try:
            p = subprocess.run(cmd, cwd=COMPOSE_DIR, capture_output=True, text=True,
                               timeout=120, encoding="utf-8", errors="replace")
        except (subprocess.TimeoutExpired, OSError):
            return False
    return p.returncode == 0


#: 建账号要启动一整套 Hydro 进程（每个约几秒 CPU）。曾经因为一次导入给全班
#: 每人起一个进程，把 2 核的机器压到没响应——所以这里串行 + 限速：
#: 同一时刻只允许一个建账号进程，两次之间至少隔 _MIN_CREATE_GAP 秒。
_CREATE_LOCK = threading.Lock()
_MIN_CREATE_GAP = 1.5
_last_create = [0.0]


def _throttle() -> None:
    gap = time.time() - _last_create[0]
    if gap < _MIN_CREATE_GAP:
        time.sleep(_MIN_CREATE_GAP - gap)


def create_user(uname: str, password: str, uid: int | None = None) -> int:
    """建 Hydro 账号（名字带考试场次，因为考号是按场次分配的）。

    串行且限速执行（见 _MIN_CREATE_GAP），避免并发启动多个 Hydro 进程压垮机器。

    这里踩过两个坑，所以逻辑写得比较硬：
      1. 账号已存在时（例如删掉比赛后用同一个 id 又建一场、考号重号），
         密码还是旧的，而名册里是新密码 → 学生永远登录不上、静默变成"系统错误"。
         所以已存在就**把密码重置成名册里的**。
      2. CLI 建失败时返回码不一定是 0，光看返回码会误判成功（"Invalid password"
         就是这么来的）。所以建完再查一次，查不到就报错。
    """
    exist = uid_of(uname)
    if exist is not None:
        if not set_password(uname, password):
            raise HydroError(f"重置账号 {uname} 密码失败（hydrooj cli 返回非零）")
        return exist
    if uid is None:
        uid = next_uid()
    mail = f"{uname.lower()}@csp.local"
    cmd = COMPOSE + ["exec", "-T", "oj-backend", "hydrooj", "cli",
                     "user", "create", mail, uname, password, str(uid)]
    with _CREATE_LOCK:
        _throttle()
        try:
            p = subprocess.run(cmd, cwd=COMPOSE_DIR, capture_output=True, text=True,
                               timeout=120, encoding="utf-8", errors="replace")
        except (subprocess.TimeoutExpired, OSError) as e:
            raise HydroError(f"创建账号失败：{e}") from e
        finally:
            _last_create[0] = time.time()
    real = uid_of(uname)
    if real is None:
        blob = ((p.stdout or "") + (p.stderr or "")).strip()
        raise HydroError(f"创建账号 {uname} 没成功：{blob[-200:]}")
    return real


# ------------------------------------------------------------------ 登录 / 提交

def login(uname: str, password: str) -> urllib.request.OpenerDirector:
    """登录并返回带 cookie 的 opener（带进程内缓存，10 分钟有效）。

    评测站对登录有风控（同一个用户名 60 秒内最多几次）。批量提交时容易撞上，
    那时返回的是 403 + "Too frequent operations"，不是密码错——所以这里等一会儿重试，
    而不是把它当成登录失败抛出去（踩过：撞风控后整批提交全变成"系统错误"）。
    """
    with _SESSION_LOCK:
        cached = _SESSIONS.get(uname)
        if cached and time.time() - cached[1] < 600:
            return cached[0]

    last_body = ""
    waits = (20, 70)          # 风控是"60 秒内最多几次"，第二次要等满一个窗口
    for attempt in range(3):
        opener = new_opener()
        _, html = _request(opener, "/login")
        m = re.search(r'name="authnChallenge"[^>]*value="([^"]*)"', html)
        challenge = m.group(1) if m else ""
        code, body = _request(opener, "/login", {
            "uname": uname, "password": password,
            "authnChallenge": challenge, "login_submit": "1",
        })
        last_body = body
        # Hydro 的"密码错误"(LoginError) 与"操作太频繁"(限流) 都是 403，只能靠正文区分：
        # 密码错误直接抛错，别当成限流去空等两轮（否则学生的密码错误会被误报成"频率限制"）。
        if "密码错误" in body or "Invalid password" in body:
            raise HydroError(f"账号 {uname} 登录失败：用户名或密码错误")
        if "Too frequent" in body or "Too frequent" in html:
            if attempt < len(waits):
                wait = waits[attempt]
                print(f"[登录风控] {uname} 撞到频率限制，等 {wait} 秒后重试", flush=True)
                time.sleep(wait)
                continue
            break
        # 登录态以首页是否出现"退出"为准（跳转已由 cookie 罐处理）
        _, home = _request(opener, "/")
        if "nav_logout" not in home:
            if "密码错误" in body or "用户名或密码错误" in body or "Wrong" in body:
                raise HydroError(f"账号 {uname} 登录失败：用户名或密码错误")
            raise HydroError(f"账号 {uname} 登录失败（可能被禁止登录或触发风控）")
        with _SESSION_LOCK:
            _SESSIONS[uname] = (opener, time.time())
        return opener
    raise HydroError(f"账号 {uname} 登录失败：评测站登录频率限制（等了两轮仍然被限流）")


def submit(uname: str, password: str, pid: str, source: str, ext: str = ".cpp",
           lang: str | None = None) -> str:
    """以该账号提交代码，返回记录 ID。lang 可显式指定（OI/IOI 赛制由选手选语言）。"""
    lang = lang or LANG_BY_EXT.get(ext.lower(), "cc.cc14o2")
    try:
        opener = login(uname, password)
    except HydroError:
        # 账号是判分前刚建的（按需创建），后端可能还没把它放进用户缓存，
        # 这时登录会报"用户名或密码错误"。等一下再试一次就好——踩过：
        # 学生第一次提交直接变成"系统错误"，第二次却正常。
        time.sleep(3.0)
        opener = login(uname, password)
    code, body = _request(opener, f"/p/{pid}/submit",
                          {"lang": lang, "code": source}, timeout=60)
    m = re.search(r"/record/([0-9a-f]{24})", body)
    if m:
        return m.group(1)
    if "请先登录" in body or "nav_login" in body:
        raise HydroError(f"提交失败：{uname} 登录态失效")
    raise HydroError(f"提交到 {pid} 失败（HTTP {code}）：{body[:200]}")


# ------------------------------------------------------------------ 逐点明细

#: 单个测试点的 input/output/answer 体积上限（字节）。**截断就在这一层做**：
#: 这几个字段是 mongosh 原样打回来的原文，下游 grading.py 还要把它们写进
#: results.json —— 一道 20 点、每点 200KB 的题不截断会把成绩文件撑爆。
CASE_TEXT_LIMIT = 4096

#: 截断后跟在内容尾巴上的提示（它本身也算在 CASE_TEXT_LIMIT 的额度里）
CASE_TRUNC_SUFFIX = "\n…（内容过长，已截断）"

#: 逐点字段名候选表。
#:
#: Hydro 各版本/各评测机回传的逐点结构并不统一：上游 hydrojudge 的 objective.ts
#: 只回传 {subtaskId, id, time, memory, status, score, message}；有的部署（或打了补丁
#: 的评测机）会把这一点的输入、学生输出、标准答案一并塞回来，键名还可能是
#: in/out/ans、userOutput/stdOutput 之类。所以这里一个名字都不写死——按候选表
#: 逐层 .get() 兜底，全都取不到就给默认值：宁可少显示，也绝不抛异常
#: （本模块跑在判分线程里，抛异常会让提交永远卡在"判题中"）。
_CASE_KEYS: dict[str, tuple[str, ...]] = {
    "no":     ("id", "caseId", "case_id", "caseNo", "case_no", "case", "no", "num", "index"),
    "status": ("status", "verdict", "state", "result"),
    "time":   ("time", "timeUsed", "time_used", "cpuTime", "timeCost", "runtime"),
    "memory": ("memory", "memoryUsed", "memory_used", "mem"),
    "score":  ("score", "points", "value"),
    "input":  ("input", "in", "inData", "inputData", "stdin", "data"),
    "output": ("output", "out", "userOutput", "user_out", "actualOutput",
               "actual_output", "stdout", "data"),
    "answer": ("answer", "ans", "expected", "expectedOutput", "expected_output",
               "stdOutput", "std_out", "juryAnswer", "correctAnswer"),
    "message": ("message", "msg", "judgeText", "judge_text", "detail", "info", "text"),
    "subtask": ("subtaskId", "subtask_id", "subtask", "subTask"),
}

#: testCases[i] 里面可能再包一层字典才放真东西（testCases[i].case / .data / .detail …），
#: 解析时会把这些子字典也摊平进来一起找。
_CASE_WRAPPERS = ("case", "data", "detail", "info", "caseInfo", "judge", "judgeInfo")


#: EJSON 规范模式（canonical）会把数字包成 {"$numberInt": "5"} 这种壳；
#: 现在用的是宽松模式（默认，直接给数字），但两种情况都接得住。
_EJSON_NUM_KEYS = ("$numberInt", "$numberLong", "$numberDouble", "$numberDecimal")


def _unwrap(value):
    """拆掉 EJSON 的数字壳；别的原样返回。"""
    if isinstance(value, dict) and len(value) == 1:
        for key in _EJSON_NUM_KEYS:
            inner = value.get(key)
            if isinstance(inner, str):
                return inner
    return value


def _as_int(value, default: int = 0) -> int:
    """尽力把值读成 int（12、"12"、"12.0" 都认）；读不出就给默认值。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _num_or_none(value):
    """time / memory 这类"有就有、没有就是 None"的数值（页面靠 _fmt_ms 显示成 "-"）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _as_text(value) -> str:
    """只把字符串和数字当内容；字典、列表、None 一律当"取不到"。"""
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _pick(layers: list[dict], field: str):
    """在若干层字典里按候选键名找第一个存在的字段，返回 (找到没有, 值)。

    只认标量（字符串 / 数字）：字典、列表是"再包一层"的容器（见 _CASE_WRAPPERS），
    不能当成内容，否则 ``testCases[i].data = {input, output, answer}`` 这种结构里
    的 data 会被当成输入本身，真正的输入反而取不到。
    """
    for layer in layers:
        for key in _CASE_KEYS[field]:
            value = _unwrap(layer.get(key))
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return True, value
    return False, None


def _case_layers(item: dict, depth: int = 3) -> list[dict]:
    """把 testCases[i] 摊成若干层：它自己，加上里面可能包着内容的子字典。

    有的部署会写成 ``testCases[i].case.id`` / ``testCases[i].data.input``，
    所以按 _CASE_WRAPPERS 往下钻（限深度，防意外自引用）。
    """
    layers = [item]
    if depth <= 0:
        return layers
    for key in _CASE_WRAPPERS:
        sub = item.get(key)
        if isinstance(sub, dict) and sub is not item:
            layers.extend(_case_layers(sub, depth - 1))
    return layers


def _case_no(value, ordinal: int) -> str:
    """测试点号：数字补零成两位（1 → "01"，与页面上一致）；取不到就用数组序号。"""
    if isinstance(value, bool):
        value = None
    if isinstance(value, (int, float)):
        return "%02d" % int(value)
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "%02d" % ordinal
    if re.fullmatch(r"\d{1,9}", text):
        return "%02d" % int(text)
    return text


def _clip(text: str, limit: int = CASE_TEXT_LIMIT) -> tuple[str, bool]:
    """按 UTF-8 字节数截断，连结尾提示一起不超过 limit；返回 (内容, 是否截断)。"""
    if not text:
        return "", False
    try:
        raw = text.encode("utf-8", "ignore")
    except Exception:            # 理论上不会发生，兜底而已
        return "", False
    if len(raw) <= limit:
        return text, False
    keep = max(0, int(limit) - len(CASE_TRUNC_SUFFIX.encode("utf-8", "ignore")))
    return raw[:keep].decode("utf-8", "ignore") + CASE_TRUNC_SUFFIX, True


def _blank_case(ordinal: int, status: int = 0) -> dict:
    """一条最小的逐点记录（只有点号和判定）：管理端至少不会白屏。"""
    return {
        "no": "%02d" % ordinal,
        "status": status,
        "status_text": status_text(status),
        "time": None,
        "memory": None,
        "score": 0,
        "input": "",
        "output": "",
        "answer": "",
        "truncated": False,
        "truncated_fields": [],
        "has_content": False,
        "message": "",
        "subtask": "",
    }


def _parse_case(item, ordinal: int) -> dict:
    """解析一个测试点；item 不是字典时也照样返回一条最小记录。"""
    if not isinstance(item, dict):
        # 少数部署把逐点结果直接存成状态码数组（[1, 1, 2, …]）
        return _blank_case(ordinal, _as_int(item, 0))
    layers = _case_layers(item)
    _, raw_no = _pick(layers, "no")
    _, raw_status = _pick(layers, "status")
    _, raw_time = _pick(layers, "time")
    _, raw_memory = _pick(layers, "memory")
    _, raw_score = _pick(layers, "score")
    _, raw_message = _pick(layers, "message")
    _, raw_subtask = _pick(layers, "subtask")
    status = _as_int(raw_status, 0)
    case = {
        "no": _case_no(raw_no, ordinal),
        "status": status,
        "status_text": status_text(status),
        "time": _num_or_none(raw_time),
        "memory": _num_or_none(raw_memory),
        "score": _as_int(raw_score, 0),
        "message": _as_text(raw_message),
        "subtask": _as_text(raw_subtask),
    }
    truncated_fields = []
    for field in ("input", "output", "answer"):
        _, raw_text = _pick(layers, field)
        text, clipped = _clip(_as_text(raw_text))
        case[field] = text
        if clipped:
            truncated_fields.append(field)
    case["truncated"] = bool(truncated_fields)
    case["truncated_fields"] = truncated_fields
    # 区分"学生没输出"和"这条记录里根本没存内容"，页面才不会把两者都说成"没有输出"
    case["has_content"] = any(case[f] for f in ("input", "output", "answer"))
    return case


def parse_testcases(raw_cases) -> list[dict]:
    """把 record.testCases 解析成逐点数组（每一项都是普通 dict，可直接落盘 / 渲染）。

    每一项的键（前 9 个是约定结构，其余是给页面用的补充）::

        {no, status, status_text, time, memory, score, input, output, answer,
         truncated, truncated_fields, has_content, message, subtask}

    约定：
      * ``no`` 是补零字符串（"01"）；取不到号时退化成数组里的序号
      * ``time`` / ``memory`` 取不到是 ``None``（页面显示 "-"），不是 0
      * ``input`` / ``output`` / ``answer`` 永远是字符串（取不到是 ""）；
        超过 CASE_TEXT_LIMIT（4KB）的截断，并在 ``truncated`` / ``truncated_fields``
        上标出来
      * ``message`` 是该点的评测信息，``subtask`` 是所属子任务 id（可能为空）
      * 没有 testCases（老记录、编译错误、系统错误、还没评完）→ 返回 **空数组**
      * **本函数不抛异常**
    """
    if not isinstance(raw_cases, (list, tuple)):
        return []
    out: list[dict] = []
    for ordinal, item in enumerate(raw_cases, 1):
        try:
            out.append(_parse_case(item, ordinal))
        except Exception:
            # 单个点解析失败不能拖垮整批查询（判分线程会静默死掉）
            out.append(_blank_case(ordinal))
    return out


def record_statuses(record_ids: list[str], with_cases: bool = True) -> dict[str, dict]:
    """批量查评测状态（默认连逐点明细一起取）。

    每个 row 除了原有的 status / score / time / memory / compilerTexts / judgeTexts，
    还会多一个 ``testcases``：逐点数组（结构见 parse_testcases）。原始的 ``testCases``
    字段**不往上传**——里面可能是几百 KB 的输入原文，截断已经在 parse_testcases 里做完。

    ``with_cases=False`` 只取汇总字段：等评测时每几秒轮询一次，没必要把大字段反复拖回来。
    """
    if not record_ids:
        return {}
    ids = ",".join(f'ObjectId("{r}")' for r in record_ids)
    fields = "status:1,score:1,time:1,memory:1,compilerTexts:1,judgeTexts:1"
    if with_cases:
        fields += ",testCases:1"
    data = _mongosh_json(
        f"db.record.find({{_id:{{$in:[{ids}]}}}},{{{fields}}}).toArray()"
    ) or []
    # EJSON 里的 _id 是 {"$oid": "..."}
    out = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        rid = row.get("_id", {})
        rid = rid.get("$oid") if isinstance(rid, dict) else rid
        if not rid:
            continue
        raw_cases = row.pop("testCases", None)
        if raw_cases is None:
            raw_cases = row.pop("testcases", None)
        row["testcases"] = parse_testcases(raw_cases)
        out[str(rid)] = row
    return out


def wait_records(record_ids: list[str], timeout_s: float = 420.0,
                 interval: float = 4.0) -> dict[str, dict]:
    """等所有记录评测结束。

    注意：编译和评测都要时间（多题并行时更久），必须等到状态离开
    等待/评测中/编译中/取数据这些中间态，否则会误判成 0 分。

    轮询期间只取汇总字段（with_cases=False）：逐点明细里可能有几百 KB 的输入输出，
    编译的那几分钟里每 4 秒拖一遍太亏（评测机只有 2 核）。评完之后再整体取一次明细，
    所以返回值里的每一行都带 ``testcases``（超时的那条是空数组）。
    """
    pending = set(record_ids)
    result: dict[str, dict] = {}
    deadline = time.time() + timeout_s
    while pending and time.time() < deadline:
        got = record_statuses(sorted(pending), with_cases=False)
        for rid, row in got.items():
            if _as_int(_unwrap(row.get("status")), 0) not in STATUS_PENDING:
                result[rid] = row
                pending.discard(rid)
        if pending:
            time.sleep(interval)
    if result:
        try:
            detail = record_statuses(sorted(result))          # 带逐点明细
        except HydroError:
            detail = {}                                       # 明细取不到也不能让判分挂掉
        for rid, row in detail.items():
            result[rid] = row
    for row in result.values():
        row.setdefault("testcases", [])
    for rid in pending:
        result[rid] = {"status": 8, "score": 0, "timeout": True, "time": None,
                       "memory": None, "testcases": []}
    return result


# ------------------------------------------------------------------ 题目

def list_problems() -> list[dict]:
    """列出站点上的题目（给教师端选题用）。"""
    data = _mongosh_json(
        'db.document.find({docType:10,domainId:"system"},{docId:1,pid:1,title:1})'
        '.sort({docId:1}).toArray()'
    ) or []
    out = []
    for row in data:
        out.append({
            "docId": row.get("docId"),
            "pid": row.get("pid") or f'#{row.get("docId")}',
            "title": row.get("title") or "(无标题)",
        })
    return out


def problem_exists(pid: str) -> bool:
    data = _mongosh_json(f'db.document.find({{docType:10,pid:"{pid}"}},{{docId:1}}).toArray()')
    return bool(data)


def problem_statement(pid: str, cache_dir: str = "", ttl: int = 6 * 3600) -> str:
    """读题面（Markdown 原文）。

    题面存在评测站的题目文档里（`content` 字段，可能是 {zh: "..."} 也可能直接是字符串）。
    这里带本地缓存（`<cache_dir>/<pid>.md`），避免每次翻页都去查数据库；
    缓存超过 ttl 秒会重新取一次（老师改了题面也会跟着更新）。
    """
    pid = str(pid or "").strip()
    if not pid:
        return ""
    path = os.path.join(cache_dir, f"{pid}.md") if cache_dir else ""
    if path and os.path.isfile(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, encoding="utf-8") as f:
            return f.read()
    js = (f'const d=db.document.findOne({{docType:10,pid:{json.dumps(pid)}}},{{content:1}}); '
          f'if(!d){{print("")}} else {{const c=d.content; '
          f'print(typeof c==="string" ? c : (c&&(c.zh||c["zh-CN"]||Object.values(c)[0]))||"")}}')
    try:
        text = _mongosh(js, timeout=30)
    except HydroError:
        text = ""
    # mongosh 的输出可能带前导日志行，取最后一段像样的内容
    text = (text or "").strip()
    if not text or text == "undefined":
        text = ""
    # Hydro 的多语言题面有时是 JSON 字符串（{"zh": "..."}），取中文那一份
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                for key in ("zh", "zh-CN", "zh_CN"):
                    if isinstance(loaded.get(key), str) and loaded[key].strip():
                        text = loaded[key]
                        break
                else:
                    vals = [v for v in loaded.values() if isinstance(v, str) and v.strip()]
                    text = vals[0] if vals else ""
        except (ValueError, TypeError):
            pass
    if path and text:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    if not text and path and os.path.isfile(path):     # 取不到就用旧缓存
        with open(path, encoding="utf-8") as f:
            return f.read()
    return text


def set_problem_statement(pid: str, text: str) -> dict:
    """**改题面**：把 Markdown 原文写回评测站题目文档的 `content` 字段。

    题面的"真身"在评测站那边（`problem_statement()` 读的就是它），考试服务这边
    `data/statements/<pid>.md` 只是**缓存**。所以改题面必须写回评测站，否则缓存一过期
    （6 小时）就会把老师的修改"冲掉"、题面自己变回去。

    `content` 有两种形状，都要照顾到（别把多语言的另一份挤掉）：
      * 字符串      —— 直接 `$set: {content: 新文本}`
      * `{zh: ...}` —— 只改 `content.zh` 这一个键（用点路径，保留其它语言）

    文本**先 base64 再拼进 JS**：题面里有引号/反斜杠/换行/中文都很正常，
    直接拼字符串要么转义出错、要么把命令撑爆（base64 只占 4/3，且全是 ASCII）。

    返回 `{"ok", "field", "modified", "error"}`；`ok=False` 时 `error` 给人看。
    """
    import base64
    pid = str(pid or "").strip()
    if not pid:
        return {"ok": False, "error": "没有指定题目标识"}
    # 换行统一成 \n：`problem_statement()` 读的时候本来就经过一轮"通用换行"归一
    # （容器文本模式把 \r\n 收成 \n），这里跟着归一，于是"读出来原样存回去"= 真的没改动
    # （不然每次保存都会因为换行风格被算成一次修改）。Markdown 不看换行风格，安全。
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    js = (
        "(() => {"
        f' const key = {{docType:10, pid:{json.dumps(pid)}}};'
        " const d = db.document.findOne(key, {content:1});"
        ' if (!d) return {ok:false, error:"评测站上没有这道题"};'
        f' const t = Buffer.from("{b64}", "base64").toString("utf8");'
        ' let field = "content";'
        ' const c = d.content;'
        ' if (c && typeof c !== "string") {'
        '   field = "content." + (Object.prototype.hasOwnProperty.call(c, "zh")'
        '                         ? "zh" : (Object.keys(c)[0] || "zh"));'
        ' }'
        ' const patch = {}; patch[field] = t;'
        ' const r = db.document.updateOne(key, {$set: patch});'
        ' return {ok: r.matchedCount === 1, field: field, modified: r.modifiedCount};'
        "})()"
    )
    try:
        data = _mongosh_json(js, timeout=60)
    except HydroError as e:
        return {"ok": False, "error": f"写回评测站失败：{e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "写回评测站没有返回结果（容器没起来？）"}
    if not data.get("ok"):
        return {"ok": False, "error": str(data.get("error") or "写回评测站失败")}
    return {"ok": True, "field": str(data.get("field") or "content"),
            "modified": int(data.get("modified") or 0)}


# ------------------------------------------------------------------ 题目数据文件（补输入/答案）

#: 单个文件的读取上限（超过就截断）
CASE_FILE_LIMIT = 4096
#: 容器内文件路径前缀 → 宿主机路径前缀。评测机容器把 data/file 挂到宿主机 HYDRO_DIR 下。
_CONTAINER_FILE_PREFIX = "/data/file/"
_HOST_FILE_PREFIX = os.path.join(HYDRO_DIR, "data", "file") + "/"


def _decode_etag(etag: str) -> str:
    """把题目数据项的 etag 解成宿主机上的文件路径（解不出来返回空串）。"""
    import base64
    try:
        raw = base64.b64decode(str(etag or "")).decode("utf-8", "replace")
    except Exception:
        return ""
    if not raw.startswith(_CONTAINER_FILE_PREFIX):
        return ""
    rel = raw[len(_CONTAINER_FILE_PREFIX):]
    # 防目录穿越：只允许 xx/yyy.in 这种两段式
    if ".." in rel or rel.startswith("/"):
        return ""
    return _HOST_FILE_PREFIX + rel


def _read_text_file(path: str, limit: int = CASE_FILE_LIMIT) -> str:
    """读一个文本文件（按 UTF-8，失败退回 GBK）；超过 limit 截断并加标记。"""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        return ""
    cut = len(data) > limit
    if cut:
        data = data[:limit]
        # 别把多字节字符切断
        for _ in range(4):
            try:
                data.decode("utf-8")
                break
            except UnicodeDecodeError:
                data = data[:-1]
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    return text + ("\n…（文件过大，已截断）" if cut else "")


def problem_data_files(pid: str) -> dict:
    """题目数据文件清单：`{文件名: 宿主机路径}`，例如 `{"1.in": "/root/hydro/..."}`。

    读不到就返回空字典（不抛异常）。
    """
    pid = str(pid or "").strip()
    if not pid:
        return {}
    # 注意：_mongosh_json 会把整段包进 EJSON.stringify(...)，所以这里必须给**表达式**
    # （用 IIFE），不能写成 print(...) 语句 —— 否则拼出来是语法错误，静默返回空。
    js = ('(() => { const d = db.document.findOne({docType:10, pid:' + json.dumps(pid)
          + '}, {data:1}); return (d && d.data) ? d.data.map(x => ({n: x.name, e: x.etag})) : []; })()')
    try:
        rows = _mongosh_json(js, timeout=30) or []
    except HydroError:
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("n") or "")
        path = _decode_etag(r.get("e"))
        if name and path:
            out[name] = path
    return out


def read_problem_case(pid: str, case_id) -> tuple:
    """读某个测试点的 **输入** 与 **标准答案** → `(input_text, answer_text)`。

    `case_id` 是评测记录里 `testCases[i].id`（从 1 开始），与题目数据文件名
    `1.in` / `1.out` 一一对应（服务器上实测确认）。

    读不到就返回 `("", "")`——调用方据此显示「评测机没有保存这一点的内容」。
    """
    files = problem_data_files(pid)
    if not files:
        return "", ""
    try:
        n = int(case_id)
    except (TypeError, ValueError):
        return "", ""
    # 数据文件命名可能是 1.in 也可能是 01.in，两种都试
    for stem in (str(n), f"{n:02d}"):
        for suf in (".in", ".out", ".ans"):
            if f"{stem}{suf}" in files:
                break
    inp = ""
    ans = ""
    for stem in (str(n), f"{n:02d}"):
        if not inp and f"{stem}.in" in files:
            inp = _read_text_file(files[f"{stem}.in"])
        if not ans:
            for suf in (".out", ".ans"):
                if f"{stem}{suf}" in files:
                    ans = _read_text_file(files[f"{stem}{suf}"])
                    break
    return inp, ans


def status_text(code) -> str:
    try:
        return STATUS.get(int(code), f"未知状态({code})")
    except (TypeError, ValueError):
        return str(code)
