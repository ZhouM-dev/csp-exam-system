"""通过服务内队列串行重测历史比赛，保存差异，恢复原有公开状态。

先完成全量备份，再在服务器运行：
python3 -m csp_exam.tools.rejudge_history --apply --backup /root/csp-history-backup-... --restore-published
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import time
import urllib.parse

from ..core import judge_profile, security, store


def write_json(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run(backup, *, apply=False, restore_published=False, port=8080):
    backup = Path(backup).resolve()
    if not (backup / "complete.tgz").is_file() or not (backup / "data-sha256.json").is_file():
        raise ValueError("需要已完成的完整备份与数据哈希清单")
    report_path = backup / "rejudge-report.json"
    if report_path.exists():
        raise ValueError("本次备份已有重测报告，避免重复覆盖；续跑需核对未完成比赛")
    contests = sorted(store.list_contests(), key=lambda c: int(str(c["id"])[1:]) if str(c["id"])[1:].isdigit() else 0, reverse=True)
    report = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "profile": judge_profile.load(),
              "backup": str(backup), "contests": [], "complete": False}
    cookie = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
    def post(path, fields):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
        try:
            conn.request("POST", path, urllib.parse.urlencode(fields), headers={"Cookie": cookie,
                "Content-Type": "application/x-www-form-urlencoded"})
            response = conn.getresponse()
            return response.status, response.read()
        finally: conn.close()
    before = {c["id"]: {"results": store.load_results(c["id"]), "published": bool(store.load_exam(c["id"]).get("released"))} for c in contests}
    write_json(backup / "pre-rejudge-results.json", before)
    for contest in contests:
        cid = contest["id"]
        old = before[cid]["results"]
        summary = {"cid": cid, "title": contest["title"], "rule": store.rule_of(contest)["key"],
                   "records": len(old), "was_published": before[cid]["published"],
                   "queued": 0, "done": 0, "blocked": [], "changed": [], "restored_publication": False}
        report["contests"].append(summary)
        if not old or not apply:
            summary["skipped"] = "无提交" if not old else "预览"
            write_json(report_path, report)
            continue
        status, data = post("/admin/judge?format=json", {"action": "rejudge", "c": cid})
        response = json.loads(data)
        if status != 200 or not response.get("ok"):
            summary["error"] = response.get("message") or f"HTTP {status}"
            write_json(report_path, report)
            print(json.dumps({"cid": cid, "error": summary["error"]}, ensure_ascii=False), flush=True)
            continue
        summary["queued"] = response["queued"]
        write_json(report_path, report)
        print(json.dumps({"cid": cid, "queued": summary["queued"], "state": "started"}, ensure_ascii=False), flush=True)
        deadline, last_done = time.monotonic() + 7200, -1
        while True:
            current = store.load_results(cid)
            active = [k for k, r in current.items() if r.get("judging") or r.get("submission_state") in ("queued", "judging")]
            summary["done"] = sum(r.get("submission_state") == "done" and bool(r.get("judge_revision")) for r in current.values())
            if summary["done"] != last_done:
                last_done = summary["done"]
                write_json(report_path, report)
                print(json.dumps({"cid": cid, "done": last_done, "active": len(active)}, ensure_ascii=False), flush=True)
            if not active: break
            if time.monotonic() >= deadline:
                summary["error"] = "重测超时，保留队列与未公布状态；不继续追加任务"
                write_json(report_path, report)
                raise RuntimeError(summary["error"])
            time.sleep(2)
        for kh, result in current.items():
            if result.get("submission_state") != "done" or any(p.get("pending") for p in result.get("problems", {}).values()):
                summary["blocked"].append({"kaohao": kh, "reason": result.get("rejudge_error") or next(
                    (p.get("system_error") for p in result.get("problems", {}).values() if p.get("pending")), "评测未完成")})
                continue
            if not result.get("judge_profile") or not result.get("evaluation_completed_at"):
                raise RuntimeError("成绩缺少新评测环境证据，不能恢复公布")
            prev, total = store.entry_total(old.get(kh, {})), store.entry_total(result)
            if prev != total:
                summary["changed"].append({"kaohao": kh, "name": store.student_name(cid, kh), "old": prev, "new": total,
                    "problems": {key: {"old": old.get(kh, {}).get("problems", {}).get(key, {}).get("score", 0),
                                      "new": item.get("score", 0), "verdict": item.get("status_text", "")}
                                 for key, item in result.get("problems", {}).items()}})
        if restore_published and summary["was_published"] and not summary["blocked"]:
            status, _ = post("/admin/release?" + urllib.parse.urlencode({"c": cid}), {"released": "1"})
            summary["restored_publication"] = status == 302 and bool(store.load_exam(cid).get("released"))
            if not summary["restored_publication"]: summary["error"] = "公开恢复被服务拒绝，需核对版本或状态"
        write_json(report_path, report)
        print(json.dumps({k: summary[k] for k in ("cid", "done", "restored_publication")}
                         | {"blocked": len(summary["blocked"]), "changed": len(summary["changed"])}, ensure_ascii=False), flush=True)
    report["complete"] = True
    report["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-published", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    report = run(args.backup, apply=args.apply, restore_published=args.restore_published, port=args.port)
    print(json.dumps({"complete": report["complete"], "contests": len(report["contests"]),
                      "done": sum(c["done"] for c in report["contests"]),
                      "blocked": sum(len(c["blocked"]) for c in report["contests"])}), flush=True)


if __name__ == "__main__": main()
