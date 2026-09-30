"""HTTP 服务：把两个端的页面和接口挂到路由上

这里只做「路由 + 请求解析 + 会话」，页面渲染在 web/*_pages.py，
业务规则在 core/。启动：python3 -m csp_exam（或 run.py）。"""

from __future__ import annotations




import traceback
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

from ..core import store
from ..config import JUDGE_SLOTS
from ..core.util import log
from ..config import PORT, PS_DOC, PS_JOBS, PS_JOBS_LOCK
from ..core.security import (ADMIN_COOKIE, make_admin_cookie, parse_admin_cookie,
                              parse_cookie)
from .multipart import _decode_name, _fix_filename, _read_zip  # noqa: F401
from .urls import admin_url, cid_query
from .ui import page
from .student_pages import StudentPages
from .admin_pages import AdminPages


class Handler(StudentPages, AdminPages, BaseHTTPRequestHandler):
    """请求入口：会话/表单/JSON 这些基础工具在这里，具体页面见两个 mixin。"""

    def _send(self, body: bytes, status: int = 200, cookie: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str, cookie: str = "") -> None:
        self.send_response(302)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cookies(self) -> dict:
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                out[k] = v
        return out

    def _session(self) -> tuple[str, str] | None:
        """学生会话：(比赛 id, 考号)，或者 None。"""
        return parse_cookie(self._cookies().get("csp", ""))

    def _student(self, cid: str = "") -> str | None:
        """取当前会话的考号；给了 cid 就要求会话属于这场考试。

        考号按场次随机分配，所以"拿着 A 场的考号去 B 场"必须挡住——
        否则会串到别人名下（B 场的同号考生）。
        """
        s = self._session()
        if not s:
            return None
        scid, kaohao = s
        if cid and scid != cid:
            return None
        return kaohao

    def _serve_static(self, path: str, prefix: str, root: str) -> None:
        """提供包内静态文件（KaTeX 字体/脚本）。只允许白名单后缀，且必须落在 root 里。"""
        rel = urllib.parse.unquote(path[len(prefix):]).replace("\\", "/").lstrip("/")
        safe = os.path.normpath(rel)
        if not rel or safe.startswith("..") or os.path.isabs(safe):
            self._send(page("文件不存在", self._flash("err", "路径不对。")), 404)
            return
        if os.path.splitext(safe)[1].lower() not in (".css", ".js", ".woff2", ".woff", ".ttf"):
            self._send(page("文件不存在", self._flash("err", "不允许的文件类型。")), 404)
            return
        full = os.path.join(root, safe)
        if not os.path.isfile(full):
            self._send(page("文件不存在", self._flash("err", "没有这个静态文件。")), 404)
            return
        ctype = {".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[
                     os.path.splitext(safe)[1].lower()]
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send(page("读不到文件", self._flash("err", "读取失败。")), 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=2592000")     # 静态资源缓存 30 天
        self.end_headers()
        self.wfile.write(data)

    def _query(self) -> dict:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}

    def _flash(self, kind: str, text: str) -> str:
        return f'<div class="flash flash-{kind}">{html.escape(text)}</div>'

    def _check_admin(self, key: str) -> bool:
        """管理端认证：URL 带对密钥，或之前登过（cookie）。"""
        if key and hmac.compare_digest(key, store.admin_key()):
            return True
        return parse_admin_cookie(self._cookies().get(ADMIN_COOKIE, ""))

    def _parse_multipart(self, max_bytes: int) -> tuple[dict, dict, dict]:
        """解析 multipart。返回 (普通字段, 文件{文件名: 内容}, 文件字段名{文件名: 表单字段名})。

        **按原始字节解析，不用 email 模块**：中文文件名（题目.md、大样例/大样例.in、
        标程.cpp）在 Content-Disposition 里是原始 UTF-8 字节，email 会按 ASCII 解码成
        U+FFFD（不可还原）。踩过：整个出题文件夹上传后中文名全变乱码，题面/标程/大样例
        一个都认不出来。所以这里按 boundary 切字节，文件名用 UTF-8 解。
        """
        ctype = self.headers.get("Content-Type", "")
        n = int(self.headers.get("Content-Length") or 0)
        if n > max_bytes:
            raise ValueError(f"提交内容太大（{n // 1048576}MB），上限 {max_bytes // 1048576}MB。")
        if "multipart/form-data" not in ctype:
            raise ValueError("提交格式不对（不是 multipart/form-data）。")
        raw = self.rfile.read(n)
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            raise ValueError("提交格式不对（缺少 boundary）。")
        delim = b"--" + m.group(1).strip().encode("latin-1", "replace")

        fields: dict[str, str] = {}
        files: dict[str, bytes] = {}
        file_fields: dict[str, str] = {}
        for part in raw.split(delim)[1:]:
            if part[:2] == b"--":            # 结束分隔符
                break
            part = part.lstrip(b"\r\n")
            head, sep, body = part.partition(b"\r\n\r\n")
            if not sep:
                head, sep, body = part.partition(b"\n\n")
                if not sep:
                    continue
            if body.endswith(b"\r\n"):
                body = body[:-2]
            elif body.endswith(b"\n"):
                body = body[:-1]
            nm = re.search(rb'name="([^"]*)"', head)
            field = _decode_name(nm.group(1)) if nm else ""
            fm_bytes = None
            fm = re.search(rb'filename="([^"]*)"', head)
            if fm is not None:
                fm_bytes = fm.group(1)
            else:
                # 少数客户端用 RFC2231：filename*=UTF-8''%E9%A2%98...
                fm2 = re.search(rb"filename\*=[^']*'[^']*'([^;\r\n]*)", head)
                if fm2 is not None:
                    fm_bytes = urllib.parse.unquote(
                        fm2.group(1).decode("latin-1")).encode("utf-8")
            if fm_bytes is not None:
                fn = _decode_name(fm_bytes)
                if fn:
                    files[fn] = body
                    if field:
                        file_fields[fn] = field
                continue
            if field:
                fields[field] = body.decode("utf-8", "replace")
        return fields, files, file_fields

    # ---------- 入口（HTTP 方法分发）----------
    def do_GET(self):
        try:
            self._route_get()
        except Exception:
            log("处理 GET 出错：\n" + traceback.format_exc())
            self._send(page("出错了", self._flash("err", "服务器内部错误，请稍后重试。")), 500)

    def do_POST(self):
        try:
            self._route_post()
        except Exception:
            log("处理 POST 出错：\n" + traceback.format_exc())
            self._send(page("出错了", self._flash("err", "服务器内部错误，请稍后重试。")), 500)

    # ---------- 路由 ----------
    def _route_get(self):
        q = self._query()
        path = urllib.parse.urlparse(self.path).path

        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # ---- 题单导入接口
        if path == "/api/problemset/report":
            self._api_ps_report(q)
            return
        if path in ("/docs/problemset", "/docs/problemset.md"):
            self._serve_doc(PS_DOC, "题单导入接口规范")
            return

        # ---- 管理端
        if path == "/admin":
            key = q.get("key", "")
            if not self._check_admin(key):
                self._admin_login()
                return
            cid = q.get("c", "")
            cookie = ""
            if q.get("key"):        # 带密钥进来过一次就记住，之后从站内链接进无需再带
                cookie = f"{ADMIN_COOKIE}={make_admin_cookie()}; Path=/; Max-Age=2592000"
            msg = q.get("m", "")
            if cid:
                # _admin_contest 收的是渲染好的 HTML；_admin_home 收的是纯文本（自己包 flash）
                self._admin_contest(q, cookie, self._flash("ok", msg) if msg else "")
            else:
                self._admin_home(q, cookie, flash=msg)
            return
        if path == "/admin/scores":
            self._admin_scores(q)
            return
        if path == "/admin/student":
            self._admin_student(q)
            return
        if path == "/admin/file":
            self._admin_file(q)
            return
        if path == "/admin/groups":
            self._admin_groups(q, flash=q.get("m", ""))
            return
        if path == "/admin/print":
            self._admin_print(q)
            return
        if path == "/admin/problem":
            self._admin_problem(q, flash=q.get("m", ""))
            return
        # T-09 新增的两条：题目列表（新页）/ 取某个测试点的详情（弹窗用 JSON）
        if path in ("/admin/problems", "/admin/testcase"):
            return self._admin_problems(q) if path == "/admin/problems" else self._api_testcase(q)
        if path.startswith("/static/katex/"):
            self._serve_static(path, "/static/katex/",
                               os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "static", "katex"))
            return
        if path == "/api/problems":
            self._api_problem_catalog(q)
            return

        # ---- 学生端
        if path in ("/", "/enter"):
            # 已经登录：直接进本场考试；没登录：显示考号输入页
            # （踩过：原来这里无条件 _redirect("/")，未登录时浏览器会在 / 上无限自我跳转）
            s = self._session()
            want = q.get("c", "")
            if s:
                scid, kaohao = s
                if want and want != scid:
                    # 拿着别场的会话走这个考场的链接：清掉旧会话，显示这一场的登录页。
                    # （踩过：这里原来 redirect 到自己 → 浏览器无限重定向报 ERR_TOO_MANY_REDIRECTS）
                    if not store.get_contest(want):
                        self._send(page("考场不存在",
                                        self._flash("err", "这个考试链接不对，请找老师核对。")), 404)
                        return
                    self._login_page(contest=store.get_contest(want),
                                     cookie="csp=; Path=/; Max-Age=0")
                    return
                self._redirect(f"/hall?c={urllib.parse.quote(scid)}")
                return
            if want and not store.get_contest(want):
                self._send(page("考场不存在",
                                self._flash("err", "这个考试链接不对，请找老师核对。")), 404)
                return
            self._login_page(contest=store.get_contest(want) if want else None)
            return
        if path == "/contests":
            s = self._session()
            if not s:
                self._redirect("/")
                return
            self._contests_page(s[1])
            return
        if path == "/help":         # 考生须知（T-08 新增）；未登录也能看（考前先读一遍）
            self._help_page(q.get("c", ""))
            return
        if path in ("/hall", "/score", "/submit", "/sample", "/problem", "/file"):
            cid = q.get("c", "")
            contest = store.get_contest(cid) if cid else None
            if not contest:
                s = self._session()
                if s:
                    self._redirect(f"/hall?c={urllib.parse.quote(s[0])}")
                else:
                    self._redirect("/")
                return
            kaohao = self._student(cid)
            if not kaohao:
                # 没登录 / 拿的是别场的会话 → 回这场的登录页
                self._redirect(f"/enter?c={urllib.parse.quote(cid)}")
                return
            if kaohao not in store.load_roster(cid):
                self._send(page("不在名单里", self._flash(
                    "err", f"考号 {kaohao} 不在本场名单里。") +
                    f'<p><a class="btn" href="/enter?c={urllib.parse.quote(cid)}">重新输入考号</a></p>'), 403)
                return
            if path == "/hall":
                self._hall_page(kaohao, contest, cid)
            elif path == "/score":
                self._score_query_page(q.get("kaohao", ""), contest, cid)
            elif path == "/sample":
                self._sample_page(kaohao, contest, cid, q.get("p", ""), q.get("f", ""))
            elif path == "/problem":
                self._problem_page(kaohao, contest, cid, q.get("p", ""))
            elif path == "/file":
                # 学生看自己提交的文件（预览/下载）
                self._serve_submit_file(cid, kaohao, q.get("f", ""),
                                        download=bool(q.get("dl")), back=f"/hall?c={cid}")
            else:
                self._submit_page(kaohao, contest, cid, q.get("p", ""))
            return
        if path == "/logout":
            self._redirect("/", cookie="csp=; Max-Age=0; Path=/")
            return
        self._send(page("页面不存在", '<p>没有这个页面。<a href="/">回到首页</a></p>'), 404)

    def _route_post(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/enter":
            self._do_enter()
            return
        if path == "/upload":
            self._do_upload()
            return
        if path == "/submit":
            self._do_submit_code()
            return
        if path == "/api/problemset/import":
            self._api_ps_import()
            return
        if path == "/admin/new":
            self._admin_new()
            return
        if path == "/admin/delete":
            self._admin_delete()
            return
        if path == "/admin/roster":
            self._admin_roster()
            return
        if path == "/admin/reshuffle":
            self._admin_reshuffle()
            return
        if path == "/admin/accounts":
            self._admin_accounts()
            return
        if path == "/admin/problem":
            self._admin_problem_post()
            return
        # T-09 新增的两条：识别出题文件夹（自动填表 / 题面预览）、自己测试（跑全部测试点）
        if path in ("/admin/scan", "/admin/selftest"):
            return self._admin_scan() if path == "/admin/scan" else self._api_selftest()
        if path == "/admin/groups":
            self._admin_groups_post()
            return
        if path == "/admin/exam":
            self._admin_exam()
            return
        if path == "/admin/release":
            self._admin_release()
            return
        self._send(page("页面不存在", "<p>没有这个动作。</p>"), 404)


def main():
    store.admin_key()      # 确保密钥已生成
    contests = store.list_contests()
    log(f"比赛服务启动，端口 {PORT}，共 {len(contests)} 场比赛："
        + "、".join(f"{c['title']}({c['rule']})" for c in contests) if contests
        else f"比赛服务启动，端口 {PORT}（还没有比赛，先去管理端新建）")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务停止")
