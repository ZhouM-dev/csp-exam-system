"""Authenticated, bounded, streaming downloads of private teacher problem ZIPs."""
from pathlib import Path
import tempfile
import threading
import urllib.parse

from ..config import MAX_PS_UPLOAD
from ..core import judgelocal, localoj, problems
from ..core.problem_export import build_zip
from .ui import page

_SLOT = threading.BoundedSemaphore(1)


def route(handler, query):
    if not handler._check_admin(query.get("key", "")):
        return handler._admin_login()
    pid = query.get("pid", "").strip()
    if not judgelocal.valid_pid(pid) or pid not in localoj.problem_pids() or problems.is_deleted(pid):
        return handler._send(page("题目不存在", handler._flash("err", "题目不存在或已删除。")), 404)
    if not _SLOT.acquire(blocking=False):
        return handler._send(page("正在导出", handler._flash("err", "另一份题包正在导出，请稍后重试。")), 429)
    try:
        with tempfile.TemporaryDirectory(prefix="csp-download-") as tmp:
            output = Path(tmp) / "problem.zip"
            try:
                build_zip(output, [pid], max_bytes=MAX_PS_UPLOAD - 4096)
            except (ValueError, OSError) as e:
                return handler._send(page("导出失败", handler._flash("err", str(e))), 400)
            if output.stat().st_size > MAX_PS_UPLOAD - 4096:
                return handler._send(page("导出失败", handler._flash("err", "题包超过题目上传上限。")), 400)
            filename = (problems.code_of_pid(pid) or pid) + "-" + pid + ".zip"
            handler.send_response(200)
            handler.send_header("Content-Type", "application/zip")
            handler.send_header("Content-Disposition", "attachment; filename=problem.zip; filename*=UTF-8''" + urllib.parse.quote(filename))
            handler.send_header("Content-Length", str(output.stat().st_size))
            handler.send_header("Cache-Control", "private, no-store")
            handler.send_header("Referrer-Policy", "same-origin")
            handler.end_headers()
            try:
                with output.open("rb") as stream:
                    while chunk := stream.read(256 * 1024):
                        handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass  # Disconnected clients still release the slot and remove private files.
    finally:
        _SLOT.release()
