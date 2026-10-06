"""CSP 分数排行榜，使用本项目的计分规则和成绩公开开关。"""

from collections import OrderedDict
from copy import deepcopy
import os

from . import store


_CACHE = OrderedDict()
_CACHE_LIMIT = 32


def _signature(cid: str) -> tuple:
    paths = [os.path.join(store.DATA_DIR, "contests.json")]
    paths.extend(os.path.join(store.DATA_DIR, "contests", cid, name)
                 for name in ("exam.json", "roster.json", "results.json"))
    values = []
    for path in paths:
        try:
            st = os.stat(path)
            values.append((st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size))
        except FileNotFoundError:
            values.append(None)
    return tuple(values)


def snapshot(cid: str, *, private: bool = False) -> dict:
    # 与成绩和公开开关的写入共用锁，避免一次投影混入两个版本。
    with store._LOCK:
        contest = store.get_contest(cid)
        if not contest:
            return {"ok": False, "error": "比赛不存在"}
        key = (os.path.abspath(store.DATA_DIR), cid, private)
        signature = _signature(cid)
        cached = _CACHE.get(key)
        if cached and cached[0] == signature:
            _CACHE.move_to_end(key)
            return deepcopy(cached[1])
        state = _snapshot(cid, contest, private=private)
        # 文件版本改变即失效；不使用可能延迟撤回成绩的时间缓存。
        if _signature(cid) == signature:
            _CACHE[key] = (signature, state)
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
        return deepcopy(state)


def _snapshot(cid: str, contest: dict, *, private: bool) -> dict:
    exam = store.load_exam(cid)
    if not private and not exam.get("released"):
        return {"ok": True, "released": False, "title": contest["title"]}
    problems = exam.get("problems") or []
    all_results = store.load_results(cid)
    rows = []
    for ranked in store.ranking(cid, include_all=True, _results=all_results):
        result = all_results.get(ranked["kaohao"]) or {}
        row = {"name": ranked["name"],
               "scores": [int((ranked["problems"].get(store.problem_dir_name(int(p["no"]))) or {}).get("score") or 0) for p in problems],
               "total": ranked["total"], "pending": ranked["pending"],
               "submitted": ranked["submitted"], "rank": ranked["rank"]}
        if private:
            row["kaohao"] = ranked["kaohao"]
            row["state"] = ("stale" if "更新" in str(result.get("rejudge_error") or "") else "blocked" if result.get("rejudge_error") else result.get("submission_state") or
                            ("judging" if result.get("judging") else "error" if ranked["pending"] else "done"))
        rows.append(row)
    return {"ok": True, "released": True, "published": bool(exam.get("released")),
            "private": private, "title": contest["title"],
            "rule": store.rule_of(contest)["key"],
            "problems": [{"no": int(p["no"]), "name": store.code_of(p),
                          "full": int(p.get("full") or 100)} for p in problems],
            "rows": rows}
