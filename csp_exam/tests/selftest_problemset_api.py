"""题单文档合同：真实 HTTP、隔离题库，不运行学生程序、不修改正式题库。"""
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam import config
from csp_exam.core import judgelocal, problems, security, store
from csp_exam.web import admin_pages
from csp_exam.web.server import Handler


def project(pid="G01", complete=True, configuration=None):
    prefix = f"题目库/{pid}-加法/"
    files = {prefix + "题目.md": "# 加法\n求和。".encode(),
             prefix + "标程.cpp": b"int main(){}",
             prefix + "data/extreme.IN": b"2 3\n"}
    if complete:
        files[prefix + "data/extreme.ans"] = b"5\n"
    if configuration is not None:
        files[prefix + "problem.json"] = json.dumps(configuration).encode()
    return files


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return stream.getvalue()


class ProblemsetAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="csp-problemset-api-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.patches = [patch.object(store, "DATA_DIR", str(self.data)),
                        patch.object(judgelocal, "PROBLEMS_DIR", str(self.data / "problems")),
                        patch.object(Handler, "log_message", lambda *a: None)]
        for p in self.patches:
            p.start()
        with config.PS_JOBS_LOCK:
            config.PS_JOBS.clear()
        self.cookie = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with config.PS_JOBS_LOCK:
                running = any(j["state"] == "running" for j in config.PS_JOBS.values())
            if not running and not list((self.data / "ps-jobs").glob("*/problemset.zip")):
                break
            time.sleep(.02)
        else:
            self.fail("后台导入任务没有结束或清理文件")
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def request(self, method, path, form=None, files=None, *, cookie=None, origin=None):
        headers = {"Cookie": self.cookie if cookie is None else cookie}
        if origin is not None:
            headers["Origin"] = origin
        body = b""
        if files is not None:
            boundary = "CSPProblemsetContractBoundary"
            for key, value in (form or {}).items():
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
            for field, name, data in files:
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n\r\n').encode() + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        conn.close()
        return result

    def submit(self, files=None, form=None, **kwargs):
        if files is None:
            files = [("file", "题单.zip", zipped(project()))]
        status, _, body = self.request("POST", "/api/problemset/import", form, files, **kwargs)
        return status, json.loads(body)

    def finish(self, accepted):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, _, body = self.request("GET", accepted["report_url"])
            self.assertEqual(status, 200)
            result = json.loads(body)
            if result["state"] != "running":
                return result
            time.sleep(.02)
        self.fail("等待导入报告超时")

    def test_real_import_named_answer_and_config(self):
        cfg = {"name": "fallback", "time_ms": 2300, "memory_mb": 512, "case_count": 1}
        status, accepted = self.submit([("file", "任意文件名.zip", zipped(project(configuration=cfg)))],
                                       {"code": "G01=sum", "time": "900", "memory": "128"})
        self.assertEqual(status, 200)
        result = self.finish(accepted)
        self.assertEqual(result["state"], "done")
        report = result["report"]
        self.assertEqual(report["imported"], ["G01"])
        self.assertEqual(report["names"], {"G01": "sum"})
        self.assertRegex(report["codes"]["G01"], r"^T\d{5}$")
        self.assertEqual(judgelocal.limits_of("G01"), (2300, 512))
        self.assertEqual(judgelocal.cases_of("G01")[0]["out"], b"5\n")

    def test_job_uses_configured_data_directory(self):
        observed = []
        original = admin_pages._ps_worker
        def worker(job, path, opts):
            observed.append(Path(path))
            original(job, path, opts)
        with patch.object(admin_pages, "_ps_worker", worker):
            status, accepted = self.submit(form={"dry_run": "1"})
            self.assertEqual(status, 200)
            self.assertEqual(self.finish(accepted)["state"], "done")
        self.assertEqual(observed[0].parent.parent, self.data / "ps-jobs")

    def test_multipart_field_is_not_filename(self):
        for files in ([('wrong', 'file', zipped(project()))],
                      [('file', 'a.zip', zipped(project())), ('other', 'b.zip', zipped(project("G02")))],
                      [('file', 'a.zip', zipped(project())), ('file', 'b.zip', zipped(project("G02")))], []):
            with self.subTest(files=[f[:2] for f in files]):
                status, body = self.submit(files)
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
        self.assertFalse(judgelocal.known_pids())

    def test_legacy_zipfile_field(self):
        status, accepted = self.submit([("zipfile", "legacy.zip", zipped(project()))], {"dry_run": "on"})
        self.assertEqual(status, 200)
        self.assertEqual(self.finish(accepted)["report"]["prepared"], ["G01"])

    def test_auth_and_origin_and_query_key(self):
        key = store.admin_key()
        self.assertEqual(self.submit(cookie="")[0], 403)
        self.assertEqual(self.submit(form={"key": key}, cookie="")[0], 403)
        self.assertEqual(self.submit(origin="https://other.example")[0], 403)
        path = "/api/problemset/import?key=" + urllib.parse.quote(key)
        status, _, body = self.request("POST", path, {"dry_run": "1"},
                                       [("file", "ok.zip", zipped(project()))],
                                       cookie="", origin="https://other.example")
        self.assertEqual(status, 200)
        accepted = json.loads(body)
        self.assertNotIn(key, accepted["report_url"])
        self.assertEqual(self.request("GET", accepted["report_url"], cookie="")[0], 403)
        self.assertEqual(self.finish(accepted)["state"], "done")
        self.assertEqual(self.request("GET", accepted["report_url"] + "&key=" + urllib.parse.quote(key), cookie="")[0], 200)
        self.assertEqual(self.request("GET", "/api/problemset/report?job=missing")[0], 404)

    def test_invalid_limits_rejected_instead_of_defaulted(self):
        for field in ("time", "memory"):
            for bad in ("0", "-1", "1.5", "wrong"):
                with self.subTest(field=field, bad=bad):
                    self.assertEqual(self.submit(form={field: bad})[0], 400)

    def test_dry_run_is_precheck_not_program_verification(self):
        status, accepted = self.submit(form={"dry_run": "true"})
        self.assertEqual(status, 200)
        report = self.finish(accepted)["report"]
        self.assertTrue(report["ok"])
        self.assertEqual(report["prepared"], ["G01"])
        self.assertFalse(report["imported"])
        self.assertEqual(report["problems"][0]["status"], "validated")
        self.assertNotIn("number", report["problems"][0])
        self.assertFalse(judgelocal.known_pids())
        self.assertEqual(problems.load_codes(), {})

    def test_bad_content_is_async_failed_query_is_ok(self):
        for data in (zipped({}), zipped(project(complete=False)), b"PKnot-a-zip"):
            status, accepted = self.submit([("file", "bad.zip", data)])
            self.assertEqual(status, 200)
            result = self.finish(accepted)
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "failed")
        self.assertFalse(judgelocal.known_pids())

    def test_partial_import_is_not_rolled_back(self):
        files = {**project("G01"), **project("G02", complete=False)}
        status, accepted = self.submit([("file", "mixed.zip", zipped(files))])
        self.assertEqual(status, 200)
        report = self.finish(accepted)["report"]
        self.assertFalse(report["ok"])
        self.assertEqual(report["imported"], ["G01"])
        self.assertEqual(report["failed"], ["G02"])
        self.assertIn("G01", judgelocal.known_pids())
        status, accepted = self.submit()
        self.assertEqual(status, 200)
        result = self.finish(accepted)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["report"]["skipped"], ["G01"])
        self.assertFalse(result["report"]["imported"])

    def test_request_and_single_problem_limits_differ(self):
        with patch.object(admin_pages, "MAX_PS_UPLOAD", 128):
            self.assertEqual(self.submit()[0], 400)
        with patch.object(config, "MAX_PS_UPLOAD", 8):
            status, accepted = self.submit(form={"dry_run": "1"})
            self.assertEqual(status, 200)
            self.assertEqual(self.finish(accepted)["state"], "done")
            status, accepted = self.submit()
            self.assertEqual(status, 200)
            result = self.finish(accepted)
            self.assertEqual(result["state"], "failed")
            self.assertIn("MiB", result["report"]["problems"][0]["message"])

    def test_two_task_capacity(self):
        release = threading.Event()
        original = admin_pages._ps_worker
        def worker(job, path, opts):
            if not release.wait(10):
                raise AssertionError("capacity test timed out")
            original(job, path, opts)
        accepted = []
        try:
            with patch.object(admin_pages, "_ps_worker", worker):
                for _ in range(2):
                    status, body = self.submit(form={"dry_run": "1"})
                    self.assertEqual(status, 200)
                    accepted.append(body)
                self.assertEqual(self.submit(form={"dry_run": "1"})[0], 429)
        finally:
            release.set()
        for job in accepted:
            self.assertEqual(self.finish(job)["state"], "done")

    def test_storage_failure_releases_capacity(self):
        original = admin_pages.os.makedirs
        def fail_upload_dir(path, *args, **kwargs):
            if "ps-jobs" in Path(path).parts:
                raise PermissionError("read only")
            return original(path, *args, **kwargs)
        with patch.object(admin_pages.os, "makedirs", side_effect=fail_upload_dir):
            status, body = self.submit()
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        with config.PS_JOBS_LOCK:
            self.assertFalse(config.PS_JOBS)
        status, accepted = self.submit(form={"dry_run": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.finish(accepted)["state"], "done")

    def test_json_identity_title_and_invalid_default_name(self):
        status, accepted = self.submit([( "file", "bundle.zip", zipped(project(configuration={"id": "NEWID", "title": "JSON标题", "name": "defaultname"})))])
        result = self.finish(accepted)["report"]
        self.assertEqual(result["imported"], ["NEWID"])
        self.assertEqual(result["problems"][0]["title"], "JSON标题")
        self.assertEqual(problems.load_problem_info()["NEWID"]["title"], "JSON标题")
        status, accepted = self.submit([( "file", "bundle.zip", zipped(project(configuration={"name": "bad-name"})))], {"dry_run": "1"})
        self.assertEqual(self.finish(accepted)["report"]["failed"], ["G01"])

    def test_concurrent_imports_with_same_id_skip_instead_of_rename(self):
        accepted = [self.submit()[1] for _ in range(2)]
        reports = [self.finish(job)["report"] for job in accepted]
        self.assertEqual(sum(len(r["imported"]) for r in reports), 1)
        self.assertEqual(sum(len(r["skipped"]) for r in reports), 1)
        self.assertEqual(judgelocal.known_pids(), {"G01"})

    def test_document_urls_return_rendered_html(self):
        for path in ("/docs/problemset", "/docs/problemset.md"):
            status, headers, body = self.request("GET", path, cookie="")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("64 MiB".encode(), body)


if __name__ == "__main__":
    unittest.main()
