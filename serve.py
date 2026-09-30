#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地预览服务：带 no-cache 头，避免改了 CSS/JS 之后浏览器还拿旧版。

用法：
    python serve.py [端口]          # 默认 8081，根目录 = 本文件所在目录

为什么不用 `python -m http.server`：
    它只发 Last-Modified，浏览器会按启发式规则缓存。原型迭代时改完样式
    刷新看不到变化，很容易误判成"改动没生效"。这里显式发 no-store，
    每次刷新都拿最新的。
"""
import functools
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 安静一点，只在出错时说话
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=ROOT)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"本地预览：http://127.0.0.1:{PORT}/原型/overview.html")
    print(f"根目录：{ROOT}   （Ctrl+C 停止）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
