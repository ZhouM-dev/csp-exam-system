"""判分：提交到评测站、读回判定、按赛制写成绩

这一层不碰 HTTP，也不拼 HTML——只负责「把一次提交变成成绩表里的一条记录」。

计分按**测试点**：每点的分值 = 满分 / 测试点数，总分 = 通过点的分值之和。
题面里的「子任务」只是数据范围的分组说明，不再决定给不给分（子任务里过一个点
就拿一个点的分，不会因为同子任务里别的点没过而整段归零）。

每题的成绩记录里多一个 `testcases` 数组（逐点落盘，结构见 T-01 约定）：

    [{no, status, status_text, time, memory, score, input, output, answer}, ...]

其中 `input`/`output`/`answer` 是**有上限的**：超过阈值就截断并加标记，完整内容转存到
`data/contests/<cid>/cases/<考号>/<题号>-<点号>.txt`（见 `_pack_cases`）。
不这么做的话，一道 20 个点、每点 200KB 的题能把 results.json 撑到十几兆。
"""

from __future__ import annotations


import html
import copy
import hashlib
from pathlib import Path
import os
import re
import time
import traceback
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from .util import log, _fmt_ms, _fmt_kb
from . import wrapper
from ..config import MAX_JUDGE_QUEUE
from . import store, judge_result as verdict, judgelocal, localoj, submissions

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="csp-judge")
_QUEUE_SLOTS = threading.BoundedSemaphore(MAX_JUDGE_QUEUE)


def reserve_submission() -> bool:
    return _QUEUE_SLOTS.acquire(blocking=False)


def release_submission() -> None:
    _QUEUE_SLOTS.release()


def enqueue_submission(cid, kaohao, picked, exam, submission_id, *, reserved=False):
    if not reserved and not reserve_submission():
        raise ValueError("评测队列已满，请稍后再提交。")

    def work():
        try:
            for attempt in range(3):
                if not safe_judge_csp(cid, kaohao, picked, exam, submission_id):
                    break
                if attempt < 2:
                    time.sleep(1)
        finally:
            release_submission()
    try:
        return _EXECUTOR.submit(work)
    except Exception:
        release_submission()
        raise


def recover_submissions():
    """服务重启后恢复当前未完成提交，不重新评测已完成的历史成绩。"""
    for contest in store.list_contests():
        cid = contest["id"]
        for kaohao, entry in store.load_results(cid).items():
            sid = entry.get("submission_id")
            # 历史资料缺失不是临时沙箱错误，重启不能把它重新排队并误认证为完成。
            if sid and not entry.get("rejudge_error") and entry.get("submission_state") in ("queued", "judging", "error"):
                _QUEUE_SLOTS.acquire()
                picked = {int(k): v for k, v in (entry.get("picked") or {}).items()}
                enqueue_submission(cid, kaohao, picked, entry.get("submission_exam") or store.load_exam(cid), sid, reserved=True)


def _rejudge_sources(cid, kaohao, entry, exam):
    """重测只归档源码；历史生成的 exe/输入输出不参与上传配额。"""
    base = store.upload_dir(cid, kaohao)
    if store.rule_of(store.get_contest(cid)).get("best_of"):
        # 满分是可证明的最高分上界；非满分不能由留存的单份源码推断历史最高。
        files, picked, renamed = {}, {}, {}
        for prob in exam.get("problems", []):
            no, code = int(prob["no"]), store.code_of(prob)
            rel = str((entry.get("problems", {}).get(store.problem_dir_name(no)) or {}).get("file") or "")
            if not rel:
                raise ValueError("IOI 缺少计分提交源码和完整历史，无法重建最高分")
            original = submissions.confined_file(base, rel)
            with open(original, "rb") as source:
                data = source.read()
            name = code + "/" + code + Path(rel).suffix
            if name in files:
                raise ValueError("IOI 题目英文名重复，源码路径冲突")
            files[name], picked[no], renamed[name] = data, name, rel
        return submissions.clean_files(files), picked, [], renamed
    previous = entry.get("picked") or {}
    roots = {str(v).split("/")[1] for v in previous.values()
             if str(v).startswith("__submissions/") and len(str(v).split("/")) > 2}
    if len(roots) > 1:
        raise ValueError("最后交卷快照不一致")
    sid = entry.get("submission_id") or ""
    if re.fullmatch(r"[a-f0-9]{32}", sid) and os.path.isdir(os.path.join(base, "__submissions", sid)):
        roots = {sid}
    root = submissions.confined_file(base, "__submissions/" + next(iter(roots))) if roots else base
    if not os.path.isdir(root):
        raise ValueError("最后交卷快照已丢失")
    # 随机考号迁移保留的用户名是可信旧考号来源；只调整最外层目录。
    uname = str(store.load_roster(cid).get(kaohao, {}).get("uname", ""))
    alias = uname[len(cid) + 1:] if uname.startswith(cid + "-") else uname
    aliases = {alias} if re.fullmatch(r"[A-Z]+-[A-Z0-9]+", alias) else set()
    files, renamed = {}, {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d != "__submissions"]
        if any(os.path.islink(os.path.join(folder, d)) for d in dirs):
            raise ValueError("源码目录包含符号链接")
        for name in names:
            if os.path.splitext(name)[1].lower() not in wrapper.SOURCE_EXTS:
                continue
            rel = os.path.relpath(os.path.join(folder, name), root).replace("\\", "/")
            path = submissions.confined_file(root, rel)
            migrated = rel
            if rel.split("/")[0] in aliases and rel.split("/")[0] != kaohao:
                migrated = kaohao + "/" + rel.split("/", 1)[1]
                renamed[migrated] = rel
            if migrated in files:
                raise ValueError("旧考号目录迁移后源码路径冲突")
            with open(path, "rb") as source:
                files[migrated] = source.read()
    if not files and not roots:
        raise ValueError("原始交卷源码已丢失；不能把缺失档案判为零分")
    files = submissions.clean_files(files)
    # 防止已经被测试夹具/改题污染的旧结果被当作本场真实交卷重算。
    expected = {store.code_of(p) for p in exam.get("problems", [])}
    old_files = [str(p.get("file")) for p in entry.get("problems", {}).values() if p.get("file")]
    if (old_files and expected and not roots
            and not any(Path(f).stem in expected for f in old_files)
            and store.entry_total(entry) > 0):
        raise ValueError("旧成绩源码对应的题目与本场配置不一致，需恢复原交卷或核对题目")
    picked, missing = wrapper.pick_sources(list(files), exam.get("problems", []), strict=True,
                    strict_layout=bool(store.rule_of(store.get_contest(cid)).get("freopen")), kaohao=kaohao)
    return files, picked, missing, renamed


def rejudge_contest(cid: str) -> int:
    """备份后重测；无法重建的提交明确待补资料，不挡住其它学生。"""
    reserved, jobs = 0, []
    try:
        with store._LOCK:
            contest = store.get_contest(cid)
            if not contest:
                raise ValueError("比赛不存在")
            rule = store.rule_of(contest)
            exam, results = store.load_exam(cid), store.load_results(cid)
            targets = [(k, r) for k, r in results.items() if r.get("submitted_at")]
            if not targets:
                return 0
            if any(r.get("judging") or r.get("submission_state") in ("queued", "judging") for _, r in targets):
                raise ValueError("本场仍有提交正在评测，请完成后重测")
            for p in exam.get("problems", []):
                judgelocal.limits_of(str(p.get("pid") or ""))
                cases = judgelocal.cases_of(str(p.get("pid") or ""))
                if not cases or any(c.get("out") is None for c in cases):
                    raise ValueError("题目 " + str(p.get("pid")) + " 的测试数据或标准答案不完整")
            prepared, issues = [], {}
            for kaohao, entry in targets:
                try:
                    files, picked, missing, renamed = _rejudge_sources(cid, kaohao, entry, exam)
                    prepared.append((kaohao, files, picked, missing, renamed))
                except (ValueError, OSError) as error:
                    issues[kaohao] = str(error)
            for _ in prepared:
                if not reserve_submission():
                    raise ValueError("评测队列空间不足，请稍后重测")
                reserved += 1
            backup = os.path.join(store.DATA_DIR, "contests", cid, "rejudge", uuid.uuid4().hex)
            store._save(os.path.join(backup, "results.json"), results)
            store._save(os.path.join(backup, "exam.json"), exam)
            snapshots = []
            for kaohao, files, picked, missing, renamed in prepared:
                sid, snapshot = submissions.archive(cid, kaohao, files)
                picked = {n: f"{snapshot}/{rel}" for n, rel in picked.items()}
                snapshots.append((kaohao, sid, picked, missing, renamed))
            exam["released"] = False
            store.save_exam(cid, exam)
            store.update_contest(cid, released=False)
            keys = {store.problem_dir_name(int(p["no"])) for p in exam.get("problems", [])}
            for kaohao, entry in targets:
                entry.update(rejudge=True, rejudge_backup=backup)
                if kaohao in issues:
                    entry.update(judging=False, submission_state="error", rejudge_error=issues[kaohao])
                    store.put_result(cid, kaohao, entry)
            for kaohao, sid, picked, missing, renamed in snapshots:
                entry = results[kaohao]
                entry.pop("rejudge_error", None)
                entry.pop("rejudge_proof", None)
                entry.update(submission_id=sid, picked={str(k): v for k, v in picked.items()},
                             missing_sources=missing, judging=True, submission_state="queued",
                             submission_exam=copy.deepcopy(exam), judge_rule=dict(rule),
                             rejudge_renamed_paths=renamed, rejudge_best_unverified=bool(rule.get("best_of")),
                             problems={k: v for k, v in entry.get("problems", {}).items() if k in keys})
                store.put_result(cid, kaohao, entry)
                jobs.append((kaohao, sid, picked))
        for kaohao, sid, picked in jobs:
            reserved -= 1
            enqueue_submission(cid, kaohao, picked, exam, sid, reserved=True)
        return len(jobs)
    finally:
        for _ in range(reserved):
            release_submission()

# ------------------------------------------------------------------ 逐点明细

#: results.json 里单块内容（输入/学生输出/标准答案）最多保留多少字符
_CASE_KEEP = 4096
#: 超过这个长度才裁、才转存。定得比 _CASE_KEEP 宽一倍是有意的：逐点内容是 T-04
#: 从评测站取回来的，那一层已经截到 4KB 并加了标记，这里不该把它的成果再截一遍；
#: 而万一张四点原文整段传进来（T-04 还没生效、或别的来源），这一道必须兜住——
#: 验收里「20 个点 × 200KB 不能撑爆 results.json」说的就是它。
_CASE_LIMIT = 8 * 1024
#: 转存到 cases/*.txt 时单块内容最多写多少字符（防超大数据题把磁盘写爆）
_DUMP_KEEP = 256 * 1024
#: 一道题最多落多少条逐点记录（正常题目 10~25 个点，这是防异常记录）
_MAX_CASES = 100

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z_.\-]")


def _safe_name(name: str, fallback: str = "x") -> str:
    """把一段文本洗成能安全当文件名的字符串。

    考号、题号正常都是字母数字，但数据文件可能被人手改过，拼路径前先洗一遍，
    免得 "/" 或 ".." 把文件写到考号目录外面去。
    """
    s = _SAFE_NAME_RE.sub("_", str(name or "").strip())[:64]
    return fallback if not s.strip(".") else s


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_case_list(raw) -> list:
    """逐点结果的兜底读法：数组直接用；对象（点号 → 内容）按点号排序后转数组；
    其它情况（字段缺失、老记录、编译错误、系统错误）返回空表，绝不抛异常。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        def order(k):
            s = str(k)
            return (0, int(s), "") if s.isdigit() else (1, 0, s)
        return [raw[k] for k in sorted(raw, key=order)]
    return []


def _case_text(raw: dict, keys: tuple) -> str:
    """按若干候选键取一段内容。字段名各来源叫法可能不同，取第一个非空的。"""
    for key in keys:
        v = raw.get(key)
        if v in (None, ""):
            continue
        if isinstance(v, str):
            return v
        if isinstance(v, (list, tuple)):
            return "\n".join(str(x) for x in v)
        return str(v)
    return ""


def _case_no(raw: dict, idx: int) -> str:
    """测试点号。约定是两位字符串（"01"），各种来源的写法都归一到这里。"""
    v = None
    for key in ("no", "caseNo", "case_no", "id", "tid", "index"):
        if raw.get(key) not in (None, ""):
            v = raw[key]
            break
    if v is None:
        v = idx + 1
    s = str(v).strip()
    return s.zfill(2) if s.isdigit() else (s or str(idx + 1).zfill(2))


def _norm_case(raw: dict, idx: int) -> dict:
    """把一条原始逐点记录归一成落盘结构（内容此刻还是原文，裁剪在 _pack_cases 里做）。

    T-04 的 parse_testcases() 除了约定结构，还带几个给页面用的补充字段
    （truncated / truncated_fields / has_content / message / subtask）。
    这些原样带过去，别在这一层丢掉——管理端要靠它们决定"能不能点开看详情"。
    """
    status = _int_or_none(raw.get("status"))
    status = 0 if status is None else status
    case = {
        "no": _case_no(raw, idx),
        "status": status,
        "status_text": verdict.status_text(status),
        "time": _int_or_none(raw.get("time")),
        "memory": _int_or_none(raw.get("memory")),
        "score": _int_or_none(raw.get("score")) or 0,
        "input": _case_text(raw, ("input", "in", "inData", "inputData")),
        "output": _case_text(raw, ("output", "out", "userOut", "userOutput")),
        "answer": _case_text(raw, ("answer", "ans", "expected", "std", "answerData")),
    }
    for key in ("truncated", "truncated_fields", "has_content"):
        if key in raw:
            case[key] = raw[key]
    for key in ("message", "subtask"):
        if raw.get(key):
            case[key] = _clip(str(raw[key]), keep=2000, limit=2000)
    return case


def _point_scores(full: int, n: int) -> list:
    """每个点的满分值：满分 / 点数；除不尽时余数摊给前面几个点。

    100 分 20 个点 → 每点 5 分；100 分 3 个点 → 34/33/33。这样「全部通过 = 满分」
    严格成立，逐点得分之和也正好是满分（四舍五入到每个点上就会差几分）。
    """
    if n <= 0:
        return []
    base, rem = divmod(int(full), int(n))
    return [base + 1] * rem + [base] * (n - rem)


def _clip(text: str, keep: int = _CASE_KEEP, limit: int = _CASE_LIMIT, path: str = "") -> str:
    """太长就截断并加标记（标记里写清原文多长、完整内容转存在哪）。"""
    if len(text) <= limit:
        return text
    where = f"；完整内容见 {path}" if path else ""
    return (text[:keep] +
            f"\n……（已截断：此处只保留前 {keep} 个字符，原文共 {len(text)} 个字符{where}）")


def _dump_case(cid: str, kaohao: str, code: str, case: dict) -> str:
    """把一个点的完整内容转存成 txt，返回相对 data/ 的路径（写不了就返回空串）。

    路径按约定：`data/contests/<cid>/cases/<考号>/<题号>-<点号>.txt`。
    它落在比赛目录里面，老师删比赛时会跟着一起清掉（delete_contest 删整个目录）。
    """
    rel = "contests/{}/cases/{}/{}-{}.txt".format(
        _safe_name(cid), _safe_name(kaohao), _safe_name(code), _safe_name(case["no"]))
    path = os.path.join(store.DATA_DIR, *rel.split("/"))
    body = [
        f"# {cid} {kaohao} {code} 测试点 {case['no']}",
        "# 判定 {} / 用时 {} / 内存 {} / 得分 {}".format(
            case["status_text"], _fmt_ms(case["time"]), _fmt_kb(case["memory"]), case["score"]),
    ]
    for label, key in (("输入", "input"), ("学生输出", "output"), ("标准答案", "answer")):
        text = case.get(key) or ""
        n = len(text)
        if n > _DUMP_KEEP:
            text = (text[:_DUMP_KEEP] +
                    f"\n……（内容过长，本文件只保留前 {_DUMP_KEEP} 个字符，原文共 {n} 个字符）")
        body.append(f"\n===== {label} =====\n{text}")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(body) + "\n")
    except OSError as e:
        log(f"[判分] {cid}/{kaohao} 转存测试点 {case['no']} 的内容失败：{e}")
        return ""
    return rel


def _pack_cases(cid: str, kaohao: str, code: str, raw_cases, full: int) -> list:
    """逐点结果 → 落盘结构：按点算分、裁内容、必要时转存。没有逐点数据就返回空表。

    - 计分按点：每点满分 = 满分 / 点数，**过了这个点才给这一点的分**；
      评测机自己算的分不可信（它可能还是按子任务捆的），所以这里重算。
    - 内容有上限：超限的完整内容转存到 cases/<考号>/<题号>-<点号>.txt，
      results.json 里只留前 _CASE_KEEP 个字符并标记，免得撑爆成绩表。
    """
    cases = []
    for raw in _as_case_list(raw_cases):
        if isinstance(raw, dict):
            cases.append(_norm_case(raw, len(cases)))
    if not cases:
        return []
    if not full:
        # 题库里没记满分（老数据）时按 100 兜底，与页面显示口径一致
        full = 100
        log(f"[判分] {cid}/{kaohao} {code} 没记满分，逐点计分按 100 分算")
    for case, point in zip(cases, _point_scores(full, len(cases))):
        case["score"] = point if case["status"] == verdict.STATUS_AC else 0
    for case in cases:
        if not any(len(case[k]) > _CASE_LIMIT for k in ("input", "output", "answer")):
            continue
        path = _dump_case(cid, kaohao, code, case)
        for key in ("input", "output", "answer"):
            case[key] = _clip(case[key], path=path)
        case["truncated"] = True
        if path:
            case["detail_file"] = path
    return cases


def _side_texts(row: dict) -> dict:
    """编译器报错、评测机评语的原文（有才存）。

    管理端要「编译错误时不给『未运行』，直接给报错原文」（整改清单 1.7），
    而落盘之后就再也拿不到 record 了，所以这里一并存下来。
    正常提交不会多出这两个字段。
    """
    out = {}
    for key, name in (("compilerTexts", "compile_error"), ("judgeTexts", "judge_texts")):
        v = row.get(key)
        blob = "\n".join(str(x) for x in v if x) if isinstance(v, (list, tuple)) else str(v or "")
        blob = blob.strip()
        if blob:
            out[name] = _clip(blob, keep=4000, limit=4000)
    return out


def _problem_meta(cid: str, prob_no: int, code: str = "", full: int = 0) -> tuple:
    """(题目英文名, 满分)。调用方手上就有题目对象时直接传进来，没传就去 exam.json 里找。

    查不到也不影响判分：英文名退成 p{题号}，满分退成 0（由 _pack_cases 按 100 兜底）。
    """
    if not code or not full:
        try:
            for p in store.load_exam(cid).get("problems") or []:
                if int(p.get("no") or 0) == int(prob_no):
                    code = code or store.code_of(p)
                    full = full or int(p.get("full") or 0)
                    break
        except (OSError, TypeError, ValueError) as e:
            log(f"[判分] {cid} 读 exam.json 找第 {prob_no} 题失败：{e!r}")
    return code or store.problem_dir_name(prob_no), int(full or 0)


def _clear_judging(cid: str, kaohao: str, submission_id: str = "", *, error: bool = False) -> None:
    """异常路径专用：清掉「判题中」标志，别让提交永远卡在判题中。

    这里自己也可能失败（比赛刚被删、磁盘满），那就只落日志——它本身就是兜底，
    不能再往外抛一次把调用方也带走。
    """
    try:
        # 比赛可能已经被删了。照旧写回会把 data/contests/<cid>/ 重新创建出来，
        # 变成一个「不在索引里、界面上看不到」的孤儿目录（_apply_result 已有同样的保护）。
        if not store.get_contest(cid):
            return
        with store._LOCK:
            entry = store.load_results(cid).get(kaohao, {})
            if submission_id and entry.get("submission_id") != submission_id:
                return
            entry.setdefault("problems", {})
            if not error and entry.get("rejudge_best_unverified"):
                exam = entry.get("submission_exam") or store.load_exam(cid)
                maximum = all((entry["problems"].get(store.problem_dir_name(int(p["no"]))) or {}).get("status") == 1
                              and (entry["problems"].get(store.problem_dir_name(int(p["no"]))) or {}).get("score") == int(p.get("full") or 0)
                              for p in exam.get("problems", []))
                if not maximum or not exam.get("problems"):
                    error = True
                    entry["rejudge_error"] = "IOI 留存计分源码重测未达到全部题目满分，缺完整历史，无法证明历史最高分"
                else:
                    entry["rejudge_proof"] = {"method": "retained_sources_attain_full_score_upper_bound"}
                    entry.pop("rejudge_error", None)
                entry.pop("rejudge_best_unverified", None)
            if entry.get("rejudge_error"):
                error = True
            entry["judging"] = False
            entry["submission_state"] = "error" if error else "done"
            if not error:
                entry["evaluation_completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            store.put_result(cid, kaohao, entry)
    except Exception as e:
        log(f"[判分] {cid}/{kaohao} 清 judging 标志失败：{e!r}")


def _apply_result(cid: str, kaohao: str, prob_no: int, row: dict, *, best_of: bool,
                  note: str = "", hint: str = "", file_rel: str = "",
                  full: int = 0, code: str = "", submission_id: str = "") -> dict:
    """把一次评测结果写进成绩表（按赛制决定覆盖还是取最高分）。

    计分按测试点：每点分值 = 满分 / 测试点数，总分 = 通过点的分值之和（不再按子任务
    捆绑）；逐点明细连输入/输出/标准答案一起落进 `testcases`，内容有上限（见 _pack_cases）。
    IOI 保留最高分时，`testcases` 跟着**计分的那次**提交走（不是最近一次）。

    返回的字典同时描述两件事，调用方据此写提示：
      status_text / score                 —— 当前计分的成绩（IOI 赛制是历史最高分）
      attempt_status_text / attempt_score —— 本次这一次提交的结果
      improved                            —— 本次是否刷新了最好成绩
    无论本次成绩好坏，都会落盘（提交次数、最近一次结果），
    否则 IOI 保留最高分的分支会让成绩表一直卡在"判题中"。
    """
    with store._LOCK:
        # 判分是异步的：老师有可能在判分过程中把这场比赛删了。此时如果照旧写回成绩，
        # `data/contests/<cid>/` 会被重新创建出来，变成一个"没有索引的孤儿目录"
        # （重启后永远没人看得到它，却会让验收脚本误报"有提交卡在判分中"）。
        if not store.get_contest(cid):
            log(f"[判分] 比赛 {cid} 已删除，丢弃 {kaohao} 第 {prob_no} 题的结果")
            return {"status_text": "比赛已删除", "score": 0, "tries": 0, "improved": False,
                    "attempt_status_text": "比赛已删除", "attempt_score": 0}
        key = store.problem_dir_name(prob_no)
        entry = store.load_results(cid).get(kaohao, {})
        if submission_id and entry.get("submission_id") != submission_id:
            return {"score": 0, "status_text": "旧提交已作废", "stale": True}
        entry.setdefault("problems", {})
        entry["judging"] = bool(submission_id)

        old = entry["problems"].get(key) or {}
        status = int(row.get("status", 8))
        failed = bool(row.get("note"))
        code, full = _problem_meta(cid, prob_no, code, full)
        # 逐点内容由 T-04 的 record_statuses() 带回来（字段名 testcases）。这里再兜一手
        # Hydro 记录里的原名 testCases：万一那边直接透传了没改名，也别当成"没有逐点数据"。
        cases = _pack_cases(cid, kaohao, code,
                            row.get("testcases", row.get("testCases")), full)
        attempt = {
            # 总分 = 通过点的分值之和（按点给分，不再走子任务）；
            # 没有逐点数据时（老记录、编译错误、系统错误、提交失败）只能退回评测机给的总分
            "score": (sum(int(c.get("score") or 0) for c in cases) if cases
                      else int(row.get("score") or 0)),
            "status": status,
            "status_text": verdict.status_text(status),
            "record_id": row.get("record_id", ""),
            "time": row.get("time"),
            "memory": row.get("memory"),
            "note": row.get("note", note),
            "hint": hint,
            "file": file_rel,
            "submission_id": submission_id,
            "testcases": cases,          # 逐点明细（管理端展示用；内容已限长）
            # 提交失败（没拿到评测结果）不算一次提交
            "tries": int(old.get("tries", 0)) + (0 if failed or entry.get("rejudge")
                       or (submission_id and old.get("submission_id") == submission_id) else 1),
        }
        attempt.update(_side_texts(row))    # 编译报错 / 评测机评语原文（有才存）
        for field in ("environment", "official_time_ms", "effective_time_ms", "time_scale", "calibrated", "reference_time"):
            if field in row:
                attempt[field] = row[field]
        if status == 8:
            item = dict(old) if old else {"score": 0, "status": 8, "status_text": "待重测"}
            item["pending"] = True
            item["system_error"] = attempt.get("note") or "评测系统错误，等待重测"
        elif best_of and old and int(old.get("score", 0)) >= attempt["score"] and not entry.get("rejudge_best_unverified"):
            # IOI 赛制：计分保留最高分（分数相同保留先到的），本次提交只更新提交信息
            item = dict(old)
            item["tries"] = attempt["tries"]
            item.setdefault("file", "")
            if hint:
                item["hint"] = hint
            item["improved"] = False
        else:
            item = dict(attempt)
            item["improved"] = bool(best_of and old
                                    and attempt["score"] > int(old.get("score", 0)))
        if status != 8:
            item.pop("pending", None)
            item.pop("system_error", None)
        if old:
            item["prev_score"] = int(old.get("score", 0))
        item["attempt_status_text"] = attempt["status_text"]
        item["attempt_status"] = attempt["status"]
        item["attempt_score"] = attempt["score"]
        item["attempt_record_id"] = attempt["record_id"]
        item["attempt_at"] = entry.get("submitted_at", "")
        # 评测机返回「系统错误」通常是它自己出问题了（典型：服务器重启后判分机没注册上
        # 判题会话，此后所有提交都是 SE）。这种时候最该让人一眼看出来，而不是当成 0 分。
        if status == 8 and not item.get("hint"):
            item["hint"] = ("评测机返回「系统错误」：多半不是代码问题，而是判分机没在工作"
                            "（服务器重启后常见）。请检查 go-judge.service，恢复后重测。")
        if failed:
            item["attempt_note"] = attempt["note"]
        entry["problems"][key] = item
        entry["total"] = sum(int(v.get("score") or 0) for v in entry["problems"].values())
        store.put_result(cid, kaohao, entry)
        return item




def judge_csp(cid: str, kaohao: str, picked: dict, exam: dict, submission_id: str = "") -> bool:
    """三种赛制共用的判分：交文件夹、逐题判。picked = {题号: 相对路径}。

    **判题在本地跑**（`core/judgelocal.py` + go-judge 沙箱），不再提交给 Hydro：
    以前是「改学生的代码（freopen 垫片）→ 提交给评测机 → 读回记录」，
    现在是「原样编译学生的代码 → 在沙箱里逐点跑 → 自己按 CSP 口径比对」。
    最大的差别是 **I/O 口径由我们定**（`io_mode`），同一份代码不会再因为
    "包不包垫片"判出两个结果 —— 老师反馈过的那个差异就是这么来的。

    **赛制差异只在这里分叉**（别再把它们混成一套）：

    * `freopen`（只有 CSP 为真）—— CSP 严格使用 `<英文名>.in/.out` 文件。
      OI/IOI 用 `stdin` 口径：只喂标准输入，沙箱里**不放**输入文件
      （学生按题面写的就是标准输入输出）。
    * `best_of`（只有 IOI 为真）—— IOI 每题取多次提交的最高分，交一份更差的不该覆盖。

    函数名沿用 `judge_csp` 是历史原因（调用点在 student_pages 里）。
    """
    info = store.load_roster(cid).get(kaohao)
    if not info:
        log(f"[{cid}/{kaohao}] 不在本场名单里，跳过判分")
        return
    # 本场的赛制规则：决定「I/O 口径」和「怎么计分」
    current_entry = store.load_results(cid).get(kaohao, {})
    rule = (current_entry.get("judge_rule") if submission_id and current_entry.get("submission_id") == submission_id else None) or store.rule_of(store.get_contest(cid) or {})
    io_mode = "file" if rule.get("freopen") else "stdin"
    best_of = bool(rule.get("best_of"))
    # 判分是异步的：老师可能在判分过程中把这场比赛删了。照旧写回会把
    # data/contests/<cid>/ 重新建出来，变成一个「不在索引里、界面上看不到」的孤儿目录。
    if not store.get_contest(cid):
        log(f"[判分] 比赛 {cid} 已删除，跳过 {kaohao}")
        return
    with store._LOCK:
        entry = store.load_results(cid).get(kaohao, {})
        if submission_id and entry.get("submission_id") != submission_id:
            return False
        from . import judge_profile
        profile = entry.get("judge_profile") if submission_id and entry.get("profile_submission_id") == submission_id else None
        limits = entry.get("judge_limits") if submission_id and entry.get("profile_submission_id") == submission_id else None
        if profile is None:
            profile = judge_profile.load()
        if limits is None:
            limits = {}
            for prob in exam.get("problems", []):
                if not (picked or {}).get(int(prob["no"])):
                    continue
                pid = str(prob.get("pid") or "")
                try:
                    limits[pid] = list(judgelocal.limits_of(pid))
                except ValueError as error:
                    limits[pid] = {"error": str(error)}
        if not best_of:
            active = {store.problem_dir_name(int(p["no"])) for p in exam.get("problems", [])}
            entry["problems"] = {k: v for k, v in entry.get("problems", {}).items() if k in active}
        entry.update({"problems": entry.get("problems", {}), "judging": True,
                      "submission_state": "judging", "judge_profile": profile,
                      "profile_submission_id": submission_id, "judge_limits": limits,
                      "judge_revision": hashlib.sha256(b"".join((Path(__file__).with_name(n)).read_bytes() for n in ("grading.py", "judgelocal.py", "gojudge.py", "wrapper.py"))).hexdigest()})
        store.put_result(cid, kaohao, entry)

    base_dir = store.upload_dir(cid, kaohao)
    errors = False
    for prob in exam.get("problems", []):
        no = int(prob["no"])
        code, full = store.code_of(prob), int(prob.get("full") or 0)
        rel = (picked or {}).get(no)
        if not rel:
            if not best_of:
                _apply_result(cid, kaohao, no, {"status": 2, "score": 0,
                                               "judgeTexts": ["本次交卷未提交这道题的源码"]},
                              best_of=False, full=full, code=code, submission_id=submission_id)
            continue
        if submission_id and store.load_results(cid).get(kaohao, {}).get("submission_id") != submission_id:
            return False
        try:
            src = submissions.confined_file(base_dir, rel)
            with open(src, "rb") as f:
                source = f.read()
        except (OSError, ValueError) as e:
            log(f"[{cid}/{kaohao}] 第{no}题读取源码失败：{e}")
            _apply_result(cid, kaohao, no, {"status": 8, "score": 0, "note": "源码快照无法读取"},
                          best_of=best_of, full=full, code=code, submission_id=submission_id)
            errors = True
            continue
        ext = os.path.splitext(rel)[1]
        # freopen 提示只对 CSP 有意义（OI/IOI 本来就该用标准输入输出）：
        # 现在**不改学生代码**了，这条提示纯粹是给老师看日志用的
        hint = wrapper.check_freopen(source.decode("utf-8", "replace"), code) if rule.get("freopen") else ""
        if hint:
            log(f"[{cid}/{kaohao}] {code} {hint}")
        # best_of 每个调用点都要显式传：漏一个就是 TypeError，判分线程静默死掉、
        # 提交永远卡在「判题中」（线上修过一次，别再退回）。
        if not wrapper.supports_source(ext):
            _apply_result(cid, kaohao, no, {"status": 7, "score": 0,
                                           "note": f"暂不支持 {ext} 语言，请用 .cpp 提交"},
                          best_of=best_of, file_rel=rel, code=code, full=full, submission_id=submission_id)
            continue
        pid = str(prob.get("pid") or "")
        try:
            fixed = limits[pid]
            if isinstance(fixed, dict):
                raise ValueError(fixed["error"])
            t_ms, mem_mb = fixed
            row = judgelocal.judge_source(pid, source, ext, code=code, full=full,
                                          time_ms=t_ms, memory_mb=mem_mb, io_mode=io_mode, profile=profile)
        except ValueError as e:
            row = {"status": 8, "score": 0, "note": str(e)}
        _apply_result(cid, kaohao, no, row, best_of=best_of, hint=hint, file_rel=rel,
                      code=code, full=full, submission_id=submission_id)
        if int(row.get("status") or 0) == 8:
            errors = True
            break
    _clear_judging(cid, kaohao, submission_id, error=errors)
    log(f"[{cid}/{kaohao}] {rule.get('key', '')} 判分完成")
    return errors


def safe_judge_csp(cid: str, kaohao: str, picked: dict, exam: dict, submission_id: str = "") -> bool:
    """判分线程的兜底入口：任何异常都不能让提交永久卡在"判题中"。

    判分跑在守护线程里，异常没人接时线程会静默死掉，judging 标志永远是 True，
    学生端就永远"判题中"。这里把 judge_csp 包一层：异常时落日志、清掉 judging
    标志（已写好的判分结果原样保留）。
    """
    try:
        return judge_csp(cid, kaohao, picked, exam, submission_id)
    except Exception as e:
        log(f"[{cid}/{kaohao}] 判分线程异常：{e!r}\n{traceback.format_exc()}")
        _clear_judging(cid, kaohao, submission_id, error=True)
        return True




def graded_text(got: dict, full: int) -> str:
    """判分结果的显示文字：满分→答案正确，中间分→部分正确，0 分才叫答案错误。

    CSP/OI/IOI 都是按测试点给分的，一个通过一半测试点的提交在评测机里的状态
    仍然是"答案错误"（Hydro 只分 AC/WA），但分数是实打实的部分分——直接照抄
    评测机状态会让学生和老师误解，所以这里按分数重新措辞。
    """
    if not got:
        return "未提交"
    if got.get("pending"):
        return "评测异常，待重测（旧分暂存）"
    score = int(got.get("score") or 0)
    status = int(got.get("status", 0))
    raw = got.get("status_text") or verdict.status_text(status)
    if score >= int(full or 100):
        return "答案正确"
    if score > 0:
        return f"部分正确（{score}/{full}）" if full else "部分正确"
    return raw


def graded_cell(got: dict, full: int, *, show_score: bool = True) -> str:
    """判分结果的 HTML 片段（带颜色）。show_score=False 时只给状态不给数字。"""
    if not got:
        return '<span class="muted">未提交</span>'
    score = int(got.get("score") or 0)
    cls = "ok" if score >= int(full or 100) else ("part" if score > 0 else "err")
    text = graded_text(got, full) if show_score else re.sub(r"（\d+/\d+）", "", graded_text(got, full))
    return f'<span class="{cls}">{html.escape(text)}</span>'


def attempt_text(item: dict, full: int) -> str:
    """这一次提交的判分文字（IOI 等赛制下可能与计分成绩不同）。"""
    if not item:
        return ""
    if item.get("attempt_score") is None:
        return graded_text(item, full)
    return graded_text({"score": item.get("attempt_score"),
                        "status": item.get("attempt_status"),
                        "status_text": item.get("attempt_status_text", "")}, full)
