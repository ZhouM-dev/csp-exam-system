"""完整 HTTP 交卷流程，独立端口和临时比赛，不改变生产成绩。"""

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import urllib.parse
from unittest.mock import patch

from csp_exam.core import judgelocal, problems, security, store
from csp_exam.web.server import Handler


def main():
    checks = []
    def check(label, condition):
        checks.append({"name": label, "ok": bool(condition)})
        if not condition: raise AssertionError(label)
    with tempfile.TemporaryDirectory(prefix="csp-http-regression-") as tmp, \
            patch.object(store, "DATA_DIR", tmp), \
            patch.object(judgelocal, "PROBLEMS_DIR", str(Path(tmp) / "problems")):
        cid, kh = "http_test", "GD-S12345"
        prob = {"no": 1, "pid": "P", "name": "candy", "full": 100}
        store._save(store._path("contests.json"), [{"id": cid, "title": "HTTP 验收", "rule": "CSP", "open": True}])
        store.save_exam(cid, {"problems": [prob], "released": False})
        store._save(store._path("contests", cid, "roster.json"), {kh: {"name": "测试学生"}})
        problems.save_problem_info({"P": {"time_ms": 1000, "memory_mb": 512}})
        judgelocal.store_problem("P", [{"name": "1", "in": b"2 3\n", "out": b"5\n"}])
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        student = "csp=" + security.make_cookie(cid, kh)
        admin = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
        def request(method, path, body=b"", *, cookie="", ctype="application/x-www-form-urlencoded"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
            conn.request(method, path, body=body, headers={"Cookie": cookie, "Content-Type": ctype})
            response = conn.getresponse()
            status, data = response.status, response.read()
            conn.close()
            return status, data
        def upload(files):
            boundary, body = "CSPHttpTestBoundary", b""
            for name, contents in files:
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n\r\n').encode() + contents + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            return request("POST", "/upload?c=" + cid, body, cookie=student,
                           ctype="multipart/form-data; boundary=" + boundary)
        def wait():
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                r = store.load_results(cid).get(kh, {})
                if r and not r.get("judging"):
                    check("评测状态完成", r.get("submission_state") == "done")
                    return r
                time.sleep(.1)
            raise AssertionError("交卷流程超时")
        try:
            status, data = request("GET", "/admin/judge")
            check("评测设置鉴权", status == 403)
            status, data = request("POST", "/admin/judge", b"time_scale=1.5", cookie=admin)
            check("教师可保存倍率", status == 200)
            src = b'#include <cstdio>\nint main(){freopen("candy.in","r",stdin);freopen("candy.out","w",stdout);int a,b;scanf("%d %d",&a,&b);printf("%d\\n",a+b);return 0;}'
            status, _ = upload([(kh + "/candy/candy.cpp", src)])
            check("CSP 目录交卷接收", status == 200)
            result = wait()
            check("正确交卷满分及倍率", result["total"] == 100 and result["problems"]["T1"]["effective_time_ms"] == 1500)
            _, data = request("GET", "/api/scoreboard?c=" + cid)
            check("未公布时 JSON 无成绩", "rows" not in json.loads(data))
            _, data = request("GET", "/api/scoreboard?c=" + cid + "&private=1", cookie=admin)
            check("教师可预览 CSP 分数", json.loads(data)["rows"][0]["total"] == 100)
            status, _ = upload([("../escape.cpp", src)])
            check("HTTP 路径穿越拒绝", status == 400)
            status, _ = upload([("candy/candy.cpp", src), ("candy/candy.cpp", src)])
            check("重复表单文件拒绝", status == 400)
            store.update_contest(cid, deadline=1)
            status, _ = upload([(kh + "/candy/candy.cpp", src)])
            check("截止后交卷拒绝", status == 403)
            store.update_contest(cid, deadline=0)
            status, _ = upload([("candy.cpp", src)])
            check("错误目录交卷保留回执", status == 200)
            result = wait()
            check("最后交卷缺题清零", result["total"] == 0)
            status, _ = request("POST", "/admin/release?c=" + cid, b"released=1", cookie=admin)
            check("教师公布成绩", status == 302)
            _, data = request("GET", "/api/scoreboard?c=" + cid)
            check("公布后排行榜显示当前分数", json.loads(data)["rows"][0]["total"] == 0)
            previous_sid = result["submission_id"]
            previous_tries = result["problems"]["T1"]["tries"]
            status, data = request("POST", "/admin/judge?format=json",
                                   urllib.parse.urlencode({"action": "rejudge", "c": cid}).encode(), cookie=admin)
            check("HTTP 重测进入实际队列并收回公布", status == 200 and json.loads(data)["queued"] == 1
                  and not store.load_exam(cid).get("released"))
            result = wait()
            check("重测创建独立快照且不增加交卷次数", result["submission_id"] != previous_sid
                  and result["problems"]["T1"]["tries"] == previous_tries and bool(result.get("judge_revision")))
            store.update_contest(cid, rule="IOI")
            src_std = b'#include <cstdio>\nint main(){int a,b;scanf("%d %d",&a,&b);printf("%d\\n",a+b);}'
            status, _ = upload([("candy/candy.cpp", src_std)])
            check("IOI 标准输入输出交卷", status == 200)
            result = wait()
            check("IOI 实际运行取得满分", result["total"] == 100)
            status, data = request("POST", "/admin/judge?format=json",
                                   urllib.parse.urlencode({"action": "rejudge", "c": cid}).encode(), cookie=admin)
            check("IOI 留存源码实际重测接收", status == 200 and json.loads(data)["queued"] == 1)
            result = wait()
            check("IOI 满分上界证据落盘", result["total"] == 100 and
                  result.get("rejudge_proof", {}).get("method") == "retained_sources_attain_full_score_upper_bound")
            judgelocal.store_problem("P", [{"name": "1", "in": b"2 3\n", "out": b"6\n"}])
            status, _ = request("POST", "/admin/judge?format=json",
                                urllib.parse.urlencode({"action": "rejudge", "c": cid}).encode(), cookie=admin)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and store.load_results(cid)[kh].get("judging"):
                time.sleep(.1)
            result = store.load_results(cid)[kh]
            check("IOI 未满分且缺历史明确待补资料", result["submission_state"] == "error" and bool(result.get("rejudge_error"))
                  and not result.get("rejudge_proof"))
            status, _ = request("POST", "/admin/release?c=" + cid, b"released=1", cookie=admin)
            check("IOI 待补资料无法公布", status == 302 and not store.load_exam(cid).get("released"))
            src_six = b'#include <cstdio>\nint main(){int a,b;scanf("%d %d",&a,&b);printf("%d\\n",a+b+1);}'
            status, _ = upload([("candy/candy.cpp", src_six)])
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and store.load_results(cid)[kh].get("judging"):
                time.sleep(.1)
            result = store.load_results(cid)[kh]
            check("IOI 新交卷不能掩盖缺失历史", status == 200 and result["total"] == 100
                  and result["submission_state"] == "error" and bool(result.get("rejudge_error")))
            status, _ = request("POST", "/admin/judge?format=json",
                                urllib.parse.urlencode({"action": "rejudge", "c": cid}).encode(), cookie=admin)
            result = wait()
            check("IOI 补交满分后重测可重新证明最高分", result["total"] == 100
                  and bool(result.get("rejudge_proof")) and not result.get("rejudge_error"))
        finally:
            server.shutdown()
            server.server_close()
    print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
