"""Portable ZIP round trips, corruption rejection and teacher-only downloads."""
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import importer, judgelocal, localoj, problems, security, store
from csp_exam.core.problem_export import build_zip
from csp_exam.core.runtime_lock import data_lock
from csp_exam.tools.export_library import export
from csp_exam.web.server import Handler

SOURCE = '#include <cstdio>\nint main(){freopen("sum.in","r",stdin);freopen("sum.out","w",stdout);int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);}'


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="csp-export-test-")
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(store, "DATA_DIR", str(self.root / "source")),
                        patch.object(judgelocal, "PROBLEMS_DIR", str(self.root / "source/problems"))]
        for p in self.patches:
            p.start()
        self.pid = "Q-01"
        got = problems.create_problem(self.pid, "导出题 $a_i$", {"1.in": b"2 3\n", "1.out": b"5\n"},
                                      statement="# 题面\n计算 $a+b$。\n", std_source=SOURCE,
                                      time_ms=1234, memory_mb=192, name="sum")
        self.assertTrue(got["ok"], got)
        judgelocal.store_problem(self.pid, [{"name": n, "in": b"2 3\n", "out": b"5\n"}
                                          for n in ("02", "10", "sample1", "case_a")])
        problems.remember_problem(self.pid, cases=4)
        sample_files = {"题面样例.in": b"2 3\n", "题面样例.out": b"5\n", "大样例.in": b"2 3\n",
                        "大样例.out": b"5\n", "说明.txt": "中文附件\n".encode()}
        problems.save_samples(self.pid, [[{"label": "最大范围", "in": "大样例.in", "out": "大样例.out", "extra": ["说明.txt"]}],
                                        [{"label": "题面例一", "in": "题面样例.in", "out": "题面样例.out"}]], sample_files)
        # Preserve actual manifest order rather than regrouping by kind.
        path = str(Path(problems._samples_root(self.pid)) / "manifest.json")
        manifest = store._load(path, dict)
        manifest["items"].reverse()
        store._save(path, manifest)
        self.items = manifest["items"]
        self.original = judgelocal.cases_of(self.pid)
        self.number = problems.code_of_pid(self.pid)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def package(self):
        target = self.root / "export.zip"
        build_zip(target, [self.pid])
        with zipfile.ZipFile(target) as z:
            return {n: z.read(n) for n in z.namelist()}

    def test_different_machine_keeps_all_problem_content(self):
        files = self.package()
        with patch.object(store, "DATA_DIR", str(self.root / "dest")), patch.object(judgelocal, "PROBLEMS_DIR", str(self.root / "dest/problems")):
            problems.create_problem("OTHER", "已有题", {"1.in": b"", "1.out": b""})
            got = problems.create_problem("", "", files)
            self.assertTrue(got["ok"], got)
            self.assertEqual(got["pid"], self.pid)
            self.assertNotEqual(got["number"], self.number)
            self.assertEqual(judgelocal.cases_of(self.pid), self.original)
            self.assertEqual(judgelocal.limits_of(self.pid), (1234, 192))
            self.assertEqual(localoj.problem_statement(self.pid), "# 题面\n计算 $a+b$。\n")
            self.assertEqual(problems.load_problem_info()[self.pid]["std"], SOURCE)
            self.assertEqual(problems.name_of_pid(self.pid), "sum")
            self.assertEqual(problems.load_samples(self.pid)["items"], self.items)
            self.assertEqual(Path(problems.sample_path(self.pid, "说明.txt")).read_bytes(), "中文附件\n".encode())

    def test_overwrite_keeps_local_number_and_exact_names(self):
        got = problems.create_problem(self.pid, "", self.package(), overwrite=True)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["number"], self.number)
        self.assertEqual(judgelocal.cases_of(self.pid), self.original)

    def test_corrupted_or_missing_files_do_not_modify_existing_problem(self):
        base = self.package()
        for mode in ("changed", "missing", "extra"):
            files = dict(base)
            if mode == "changed":
                files[self.pid + "/data/02.out"] = b"wrong"
            elif mode == "missing":
                del files[self.pid + "/samples/说明.txt"]
            else:
                files[self.pid + "/data/undeclared.in"] = b"extra"
            got = problems.create_problem(self.pid, "", files, overwrite=True)
            self.assertFalse(got["ok"], got)
            self.assertEqual(judgelocal.cases_of(self.pid), self.original)
            self.assertEqual(problems.load_samples(self.pid)["items"], self.items)

    def test_manifest_rejects_unsafe_sample_paths_and_bad_sizes(self):
        base = self.package()
        key = self.pid + "/csp-package.json"
        for field, value in (("name", "../secret"), ("size", 1000000), ("role", "exec")):
            files = dict(base)
            meta = json.loads(files[key])
            meta["samples"][0]["files"][0][field] = value
            files[key] = json.dumps(meta).encode()
            self.assertFalse(problems.create_problem(self.pid, "", files, overwrite=True)["ok"])

    def test_missing_answer_sample_and_symlink_fail_without_partial_zip(self):
        for path in (Path(judgelocal.cases_dir(self.pid)) / "02.out", Path(problems.sample_path(self.pid, "说明.txt"))):
            data = path.read_bytes()
            path.unlink()
            with self.assertRaises(ValueError):
                build_zip(self.root / "bad.zip", [self.pid])
            self.assertFalse((self.root / "bad.zip").exists())
            path.write_bytes(data)
        if sys.platform != "win32":
            path = Path(problems.sample_path(self.pid, "说明.txt"))
            data = path.read_bytes()
            path.unlink()
            path.symlink_to(self.root / "outside-secret")
            with self.assertRaises(ValueError):
                build_zip(self.root / "bad.zip", [self.pid])
            path.unlink()
            path.write_bytes(data)

    def test_export_in_running_server_and_offline_lock(self):
        with data_lock(store.DATA_DIR):
            self.assertEqual(build_zip(self.root / "online.zip", [self.pid])["problems"], 1)
            with self.assertRaises((RuntimeError, OSError)):
                export(self.root / "offline.zip", [self.pid])

    def test_unknown_deleted_oversized_and_existing_output(self):
        with self.assertRaises(ValueError):
            build_zip(self.root / "bad.zip", ["unknown"])
        with self.assertRaises(ValueError):
            build_zip(self.root / "bad.zip", [self.pid], max_bytes=10)
        self.assertFalse((self.root / "bad.zip").exists())
        path = self.root / "existing.zip"
        path.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            build_zip(path, [self.pid])
        self.assertEqual(path.read_bytes(), b"keep")
        problems.set_deleted(self.pid)
        with self.assertRaises(ValueError):
            build_zip(self.root / "bad.zip", [self.pid])

    def test_empty_optional_fields_clear_old_content_and_import_cli(self):
        items = problems.load_problem_info()
        items[self.pid]["std"] = ""
        items[self.pid]["name"] = ""
        problems.save_problem_info(items)
        codes = problems.load_codes()
        codes[self.pid]["name"] = ""
        problems.save_codes(codes)
        localoj.set_problem_statement(self.pid, "")
        store._save(str(Path(problems._samples_root(self.pid)) / "manifest.json"), {"items": []})
        files = self.package()
        with patch.object(store, "DATA_DIR", str(self.root / "dest")), patch.object(judgelocal, "PROBLEMS_DIR", str(self.root / "dest/problems")):
            problems.create_problem(self.pid, "旧题", {"1.in": b"", "1.out": b""}, statement="旧", name="old", std_source="old")
            got = importer.import_problemset(str(self.root / "export.zip"), overwrite=True, log=lambda *a: None)
            self.assertTrue(got["ok"], got)
            self.assertEqual(problems.name_of_pid(self.pid), "")
            self.assertEqual(problems.load_problem_info()[self.pid]["std"], "")
            self.assertEqual(localoj.problem_statement(self.pid), "")
            self.assertEqual(problems.load_samples(self.pid), {})

    def test_zip_has_no_credentials_or_student_records(self):
        cid = store.create_contest("私密考试", "CSP")["id"]
        store.save_roster(cid, {"SECRET-STUDENT": {"name": "SECRET-NAME", "pw": "SECRET-PASSWORD"}})
        files = self.package()
        self.assertTrue(all(n == "library-manifest.json" or n.startswith(self.pid + "/") for n in files))
        for private in (b"SECRET-STUDENT", b"SECRET-NAME", b"SECRET-PASSWORD", store.admin_key().encode()):
            self.assertFalse(any(private in b for b in files.values()))
        manifest = json.loads(files["library-manifest.json"])
        self.assertEqual(manifest["sha256"], {n: hashlib.sha256(b).hexdigest() for n, b in files.items() if n != "library-manifest.json"})

    def test_http_auth_download_and_upload_to_new_deployment(self):
        with patch.object(Handler, "log_message", lambda *a: None):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, path, cookie="", body=b"", content_type=""):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                conn.request(method, path, body, {"Cookie": cookie, "Content-Type": content_type})
                r = conn.getresponse()
                result = r.status, dict(r.getheaders()), r.read()
                conn.close()
                return result
            try:
                path = "/admin/problem-export?pid=" + self.pid
                admin = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
                for cookie in ("", "csp=" + security.make_cookie("c1", "student")):
                    self.assertEqual(request("GET", path, cookie)[0], 403)
                from csp_exam.web.problem_export import _SLOT
                with _SLOT:
                    self.assertEqual(request("GET", path, admin)[0], 429)
                status, headers, data = request("GET", path, admin)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "application/zip")
                self.assertIn("no-store", headers["Cache-Control"])
                self.assertIn("attachment", headers["Content-Disposition"])
                self.assertEqual(len(data), int(headers["Content-Length"]))
                for page in ("/admin/problems", "/admin/problem-detail?pid=" + self.pid):
                    self.assertIn(path.encode(), request("GET", page, admin)[2])
                self.assertEqual(request("GET", "/admin/problem-export?pid=../x", admin)[0], 404)
                with patch.object(store, "DATA_DIR", str(self.root / "dest")), patch.object(judgelocal, "PROBLEMS_DIR", str(self.root / "dest/problems")):
                    admin = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()
                    body = b'--EXPB\r\nContent-Disposition: form-data; name="data"; filename="problem.zip"\r\nContent-Type: application/zip\r\n\r\n' + data + b'\r\n--EXPB--\r\n'
                    status, _, response = request("POST", "/admin/problem", admin, body, "multipart/form-data; boundary=EXPB")
                    self.assertEqual(status, 302, response.decode(errors="replace"))
                    self.assertEqual(judgelocal.cases_of(self.pid), self.original)
                    self.assertEqual(problems.name_of_pid(self.pid), "sum")
                    self.assertEqual(problems.load_samples(self.pid)["items"], self.items)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
