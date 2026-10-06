# -*- coding: utf-8 -*-
"""验证新的 multipart 解析：中文文件名、多文件、普通字段。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
from csp_exam.core import importer, problems, store, wrapper  # noqa: E402

# 直接构造一个假的 handler，复用它的解析逻辑
from csp_exam.web import server as es


class FakeHandler(es.Handler):
    def __init__(self, body: bytes, boundary: str):
        self.rfile = type("R", (), {"read": staticmethod(lambda n: body)})()
        self.headers = {"Content-Type": 'multipart/form-data; boundary="%s"' % boundary,
                        "Content-Length": str(len(body))}


def build(parts):
    """parts: [(field, filename, content_bytes)]"""
    b = b"BOUND"
    out = b""
    for field, filename, content in parts:
        out += b"--" + b + b"\r\n"
        if filename:
            out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (field, filename)).encode("utf-8")
            out += b"Content-Type: application/octet-stream\r\n\r\n"
        else:
            out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % field).encode("utf-8")
        out += content + b"\r\n"
    out += b"--" + b + b"--\r\n"
    return out


ok = fail = 0


def check(cond, label, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (label, extra))


print("=== 中文文件名 ===")
body = build([
    ("folder", "题目库/G01-大样例测试/题目.md", "# 题面".encode()),
    ("folder", "题目库/G01-大样例测试/大样例/大样例.in", b"1 2\n"),
    ("folder", "题目库/G01-大样例测试/标程.cpp", b"int main(){}"),
])
h = FakeHandler(body, "BOUND")
fields, files, ff = h._parse_multipart(10 * 1024 * 1024)
names = sorted(files)
check("题目库/G01-大样例测试/题目.md" in files, "中文路径原样保留", names[0] if names else "")
check(len(files) == 3, "三个文件都收到", len(files))
check(ff.get("题目库/G01-大样例测试/标程.cpp") == "folder", "字段名正确关联")
check(files["题目库/G01-大样例测试/大样例/大样例.in"] == b"1 2\n", "文件内容正确")

print()
print("=== 普通字段（含中文）===")
body2 = build([
    ("pid", None, "G01".encode()),
    ("title", None, "加法测试".encode()),
    ("statement", None, "# 中文题面\n描述".encode()),
    ("folder", "题目.md", b"x"),
])
h2 = FakeHandler(body2, "BOUND")
fields2, files2, ff2 = h2._parse_multipart(10 * 1024 * 1024)
check(fields2.get("pid") == "G01", "字段 pid")
check(fields2.get("title") == "加法测试", "中文字段值", fields2.get("title"))
check("中文题面" in (fields2.get("statement") or ""), "多行中文内容")
check(len(files2) == 1, "文件与字段分得清", list(files2))

print()
print("=== 二进制内容不被破坏 ===")
blob = bytes(range(256)) * 4
body3 = build([("data", "1.out", blob)])
h3 = FakeHandler(body3, "BOUND")
_, files3, _ = h3._parse_multipart(10 * 1024 * 1024)
check(files3.get("1.out") == blob, "二进制内容一致", "%d 字节" % len(files3.get("1.out") or b""))

print()
print("=== 超过上限要报错 ===")
h4 = FakeHandler(b"x" * 100, "BOUND")
try:
    h4._parse_multipart(10)
    check(False, "超限应报 ValueError")
except ValueError as e:
    check("太大" in str(e), "超限报错", str(e)[:30])

print()
print("================== 结果：%d 项通过，%d 项失败 ==================" % (ok, fail))
sys.exit(1 if fail else 0)
