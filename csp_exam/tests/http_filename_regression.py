"""Real student resubmission after admin renames the two regular J questions.

Run on the CSP server with its private service environment loaded. Production
questions are read once, then copied into isolated storage and a temporary Hydro
sidecar. No production student, contest or result is created or changed.
"""
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
import time
import urllib.parse
from unittest.mock import patch
import zipfile

from csp_exam.core import hydro_client, judgelocal, problems, security, store, wrapper
from csp_exam.tests.live_hydro_fixture import live_hydro_fixture
from csp_exam.web.server import Handler


def main():
    # Read the actual published programs/data before dropping root privileges.
    library = problems.load_problem_info()
    originals = [(pid, old, new, dict(library[pid]), judgelocal.cases_of(pid))
                 for pid, old, new in [("J001R2T3", "label", "lights"),
                                      ("J001R2T4", "transit", "patrol")]]
    checks, calls = [], []
    def check(label, condition):
        checks.append({"name": label, "ok": bool(condition)})
        if not condition:
            raise AssertionError(label)
    actual_judge = hydro_client.judge
    def observe(*args, **kw):
        calls.append({"pid": args[0], "code": kw["code"], "io_mode": kw["io_mode"]})
        return actual_judge(*args, **kw)

    with live_hydro_fixture(prefix="csp-filename-regression-") as tmp, \
            patch.object(store, "DATA_DIR", tmp), \
            patch.object(judgelocal, "PROBLEMS_DIR", str(Path(tmp)/"problems")), \
            patch.object(hydro_client, "judge", observe), \
            patch.object(Handler, "log_message", lambda *args: None):
        cid, kh = "filename_test", "GD-J12345"
        store._save(store._path("contests.json"), [{"id": cid, "title": "文件名回归", "rule": "CSP",
            "start_at": int(time.time())-600, "end_at": int(time.time())+3600}])
        exam = {"problems": []}
        for no, (pid, old, new, info, cases) in enumerate(originals, 1):
            # Seed the old naming contract, retaining the real problem number.
            problems.assign_number(pid, title=info["title"], name=old)
            problems.remember_problem(pid, title=info["title"], name=old,
                time_ms=info["time_ms"], memory_mb=info["memory_mb"], cases=len(cases), std=info["std"])
            judgelocal.store_problem(pid, cases)
            exam["problems"].append({"no": no, "pid": pid, "title": info["title"], "name": old, "full": 100})
        store.save_exam(cid, exam)
        store._save(store._path("contests", cid, "roster.json"), {kh: {"name": "文件名测试"}})
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        student = "csp=" + security.make_cookie(cid, kh)
        admin = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
        def request(method, path, body=b"", cookie=student, ctype="application/x-www-form-urlencoded"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=60)
            conn.request(method, path, body, {"Cookie": cookie, "Content-Type": ctype})
            response = conn.getresponse()
            result = response.status, response.read()
            conn.close()
            return result
        def upload(files):
            boundary = "CSPFilenameBoundary"
            body = b"".join((f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n\r\n'.encode()
                             + data + b"\r\n") for name, data in files.items())
            body += f"--{boundary}--\r\n".encode()
            return request("POST", "/upload?c="+cid, body, ctype="multipart/form-data; boundary="+boundary)
        def sources(old=False):
            result = {kh+"/说明.txt": "按英文名提交。".encode("gbk")}
            for pid, previous, new, info, _ in originals:
                code = previous if old else new
                source = info["std"].replace(new+".in", code+".in").replace(new+".out", code+".out")
                result[f"{kh}/{code}/{code}.cpp"] = source.encode()
            return result
        def wait(previous=""):
            deadline = time.monotonic()+180
            while time.monotonic() < deadline:
                row = store.load_results(cid).get(kh, {})
                if row.get("submission_id") != previous and row.get("submission_state") in ("done", "error") and not row.get("judging"):
                    check("交卷评测完成", row["submission_state"] == "done")
                    return row
                time.sleep(.2)
            raise AssertionError("实际交卷评测超时")
        def verify(row, names):
            check("两题实际满分", row["total"] == 200)
            check("提交快照使用对应英文名", [p["name"] for p in row["submission_exam"]["problems"]] == names)
            for no, code in enumerate(names, 1):
                result = row["problems"][f"T{no}"]
                check(code+" 目录和源码留存正确", result["file"].endswith(f"/{kh}/{code}/{code}.cpp"))
                check(code+" 全部20点真实通过", result["score"] == 100 and result["status"] == 1 and len(result["testcases"]) == 20)
            check("Hydro收到本次新文件名", [c["code"] for c in calls[-2:]] == names and all(c["io_mode"] == "file" for c in calls[-2:]))
        try:
            check("旧名首次文件夹交卷接收", upload(sources(old=True))[0] == 200)
            original = wait()
            verify(original, ["label", "transit"])
            for pid, old, new, info, _ in originals:
                form = urllib.parse.urlencode({"title": info["title"], "name": new,
                    "time_ms": info["time_ms"], "memory_mb": info["memory_mb"], "statement": "# "+info["title"]}).encode()
                status, _ = request("POST", "/admin/problem-detail?pid="+pid, form, cookie=admin)
                check("教师详情页保存 "+new, status == 302)
                status, page = request("GET", "/admin/problem-detail?pid="+pid, cookie=admin)
                check("教师页面英文名 "+new, status == 200 and f'name="name" value="{new}"'.encode() in page)
                check("题库双登记同步 "+new, problems.name_of_pid(pid) == new and problems.load_problem_info()[pid]["name"] == new)
            check("本场默认名字自动同步", [p["name"] for p in store.load_exam(cid)["problems"]] == ["lights", "patrol"])
            check("旧提交快照未改写", store.load_results(cid)[kh]["submission_exam"] == original["submission_exam"])
            check("新名文件夹再次交卷接收", upload(sources())[0] == 200)
            renamed = wait(original["submission_id"])
            check("再次交卷有独立快照", renamed["submission_id"] != original["submission_id"])
            verify(renamed, ["lights", "patrol"])
            old_picked, _ = wrapper.pick_sources(list(sources(old=True)), store.load_exam(cid)["problems"],
                strict=True, strict_layout=True, kaohao=kh)
            check("旧目录不能误匹配新题", not old_picked)
            zipped = io.BytesIO()
            with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in sources().items():
                    archive.writestr(name, data)
            check("新名 ZIP 含GBK说明txt可交卷", upload({"交卷.zip": zipped.getvalue()})[0] == 200)
            verify(wait(renamed["submission_id"]), ["lights", "patrol"])
        finally:
            server.shutdown()
            server.server_close()
    print(json.dumps({"ok": True, "checks": checks, "engine_calls": calls}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
