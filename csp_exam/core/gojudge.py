#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""go-judge 客户端（判题沙箱，`https://github.com/criyle/go-judge`）。

它是**沙箱执行器**，不是 OJ：只会「编译这段代码」「按这些限额跑这个程序」，
题目数据、判分口径、成绩都归考试服务自己管（见 `judgelocal.py`）。

服务端：`systemd` 常驻 `127.0.0.1:5050`（见仓库文档与 `deploy.sh`），
**只监听本机** —— 它本身没有鉴权，安全边界就是"不对外"。

接口（v1.13 实测，别照抄别的版本的字段名）：
  * `GET  /version`        健康检查（`buildVersion`）
  * `GET  /config`         支持哪些特性（`cgroupType` 必须是 2，见 `probe()`）
  * `POST /file`           把内容存进沙箱的文件仓库，返回 fileId（**二进制安全**）
  * `GET  /file/<id>`      取回内容；`DELETE /file/<id>` 释放
  * `POST /run`            跑命令；响应**就是结果数组本身**（不是 {"results": …}）

限额字段名（v1.13）：`cpuLimit`（CPU 时间，纳秒）、`clockLimit`（墙钟，纳秒）、
`memoryLimit`（字节）、`stackLimit`、`procLimit`。**这些名字必须对** ——
写错的话服务端会当未知字段忽略，程序就"没有限额"地跑飞（所以下面有
`selftest()`：真跑一个死循环确认 TLE 拦得住）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import threading
import time

#: 沙箱地址；只允许本机，换机时用环境变量覆盖
BASE = os.environ.get("CSP_GOJUDGE_URL", "http://127.0.0.1:5050").rstrip("/")
#: 单次 HTTP 超时（跑题本身另有 clockLimit 管着，这里只是别把线程吊死）
HTTP_TIMEOUT = int(os.environ.get("CSP_GOJUDGE_TIMEOUT", "120"))

#: 文件名安全字符：fileId 是服务端给的，这里只做形状校验
_FID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_VERIFY_LOCK = threading.Lock()
_VERIFIED_AT = 0.0
_VERIFIED_INFO = {}


class GoJudgeError(Exception):
    """沙箱不可用/返回了看不懂的东西（消息可直接写日志给人看）。"""


def _post(path: str, obj, *, raw: bytes | None = None, ctype: str = "application/json"):
    data = raw if raw is not None else json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                     # noqa: BLE001
            pass
        raise GoJudgeError(f"沙箱返回 HTTP {e.code}：{detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GoJudgeError(f"连不上判题沙箱（{BASE}）：{e}") from e
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError as e:
        raise GoJudgeError(f"沙箱响应不是 JSON：{body[:200]!r}") from e


def _get(path: str) -> bytes:
    try:
        with urllib.request.urlopen(BASE + path, timeout=HTTP_TIMEOUT) as r:
            return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        raise GoJudgeError(f"沙箱取文件失败：{e}") from e


def version() -> dict:
    """沙箱版本与特性（连不上会抛 GoJudgeError）。"""
    return json.loads(_get("/version").decode("utf-8", "replace"))


def probe() -> dict:
    """体检：能不能判题。返回 {"ok", "version", "cgroup", "error"}。

    判题前值得跑一次（老师点「自己测试」、或看板上显示"判题服务正常/挂了"）：
    沙箱没起来 / 降级成 rlimit（限额不准）都要能一眼看出来。
    """
    out = {"ok": False, "version": "", "cgroup": "", "error": ""}
    try:
        v = json.loads(_get("/version").decode("utf-8", "replace"))
        c = json.loads(_get("/config").decode("utf-8", "replace"))
    except Exception as e:                                    # noqa: BLE001
        out["error"] = f"{e}"
        return out
    out["version"] = str(v.get("buildVersion") or "")
    rc = c.get("runnerConfig") or {}
    out["cgroup"] = f"v{rc.get('cgroupType')}"
    if rc.get("cgroupType") != 2:
        out["error"] = (f"沙箱不是 cgroup v2（{out['cgroup']}）—— 限额可能不准，"
                        f"检查 systemd 与 /sys/fs/cgroup 挂载")
        return out
    out["ok"] = True
    return out


def ensure_ready() -> dict:
    """每分钟核验实际沙箱编译器、cgroup 与 seccomp，错误时停止评测。"""
    global _VERIFIED_AT, _VERIFIED_INFO
    with _VERIFY_LOCK:
        if time.monotonic() - _VERIFIED_AT < 60 and _VERIFIED_INFO:
            return dict(_VERIFIED_INFO)
        p = probe()
        if not p["ok"]:
            raise GoJudgeError(p["error"])
        r = run(["/usr/bin/g++", "--version"], cpu_ms=1000, memory_mb=64)
        text = (r.get("files") or {}).get("stdout", b"").decode("utf-8", "replace")
        first = text.splitlines()[0] if text else ""
        if r.get("status") != "Accepted" or not first.endswith(" 9.3.0"):
            raise GoJudgeError("评测环境不符合 NOI Linux 2.0：要求 G++ 9.3.0，实际 " + first)
        r = run(["/bin/sh", "-c", "grep -E 'Seccomp:|NoNewPrivs:' /proc/self/status"],
                cpu_ms=1000, memory_mb=64)
        text = (r.get("files") or {}).get("stdout", b"").decode("utf-8", "replace")
        if r.get("status") != "Accepted" or "Seccomp:\t2" not in text or "NoNewPrivs:\t1" not in text:
            raise GoJudgeError("沙箱 seccomp 或 NoNewPrivs 未生效，已停止评测")
        _VERIFIED_INFO = {"compiler": first, "sandbox": p["version"], "cgroup": p["cgroup"],
                          "flags": ["-O2", "-std=c++14", "-static"]}
        _VERIFIED_AT = time.monotonic()
        return dict(_VERIFIED_INFO)


# ------------------------------------------------------------------ 文件仓库

def prepare(data: bytes) -> str:
    """以 multipart 原样上传二进制，避免 JSON UTF-8 解码改变输入/输出。"""
    boundary = "csp" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            'filename="data"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    value = _post("/file", None, raw=body,
                  ctype=f"multipart/form-data; boundary={boundary}")
    fid = value if isinstance(value, str) else (value or {}).get("fileId", "")
    if not fid or any(c not in _FID_OK for c in fid):
        raise GoJudgeError("沙箱上传文件没有返回有效 fileId")
    return fid

def fetch(file_id: str) -> bytes:
    """取回文件仓库里的内容（`copyOutCached` 存下来的编译产物就靠它）。"""
    if not file_id or any(c not in _FID_OK for c in file_id):
        raise GoJudgeError(f"fileId 形状不对：{file_id!r}")
    return _get("/file/" + urllib.parse.quote(file_id))


def drop(file_id: str) -> None:
    """释放缓存文件（判完题记得清，不然仓库会一直涨）。关不掉不报错。"""
    if not file_id or any(c not in _FID_OK for c in file_id):
        return
    try:
        req = urllib.request.Request(BASE + "/file/" + urllib.parse.quote(file_id),
                                     method="DELETE")
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:                                         # noqa: BLE001
        pass


# ------------------------------------------------------------------ 跑命令

def run(args, *, copy_in=None, stdin: bytes | str = b"", env=None,
        cpu_ms: int = 5000, clock_ms: int | None = None, memory_mb: int = 256,
        stack_mb: int = 8, proc_limit: int = 64,
        copy_out=("stdout", "stderr"), cached=None, files=None,
        copy_out_max: int = 64 * 1024 * 1024) -> dict:
    """在沙箱里跑一条命令，返回结果 dict（`status` / `time` / `memory` / `files` / `fileIds`）。

    * `copy_in` —— `{沙箱里的路径: {"content": str} 或 {"fileId": str}}`；
      测试数据/源码都**内联**传（`{"content": 原文}`，文本题够用）；
    * `cached`  —— 要**留在仓库里**的产物（编译出来的可执行文件就靠它，
      这样每个测试点不用重新编译，也不用把二进制再传一遍）。
    * `stdin`   —— 喂给进程（CSP 的"文件输入"用不着它，但标准输入输出的题要用）
    """
    # 内联 content 是**原文**（不是 base64）—— 实测：`{"content": "1 2\n"}` 就是这个字符串本身。
    # 内联只适合文本；正式评测以 prepare/fileId 传递原始字节。
    cmd = {
        "args": [str(a) for a in args],
        "env": list(env or ("PATH=/usr/bin:/bin:/usr/local/bin", "LANG=C.UTF-8")),
        "files": [{"content": stdin if isinstance(stdin, str)
                   else stdin.decode("utf-8", "replace")},
                  {"name": "stdout", "max": 64 * 1024 * 1024},
                  {"name": "stderr", "max": 1024 * 1024}],
        "cpuLimit": int(cpu_ms) * 10 ** 6,                    # 纳秒
        "clockLimit": int(clock_ms if clock_ms is not None else cpu_ms * 3 + 2000) * 10 ** 6,
        "memoryLimit": int(memory_mb) * 1024 * 1024,           # 字节
        "stackLimit": int(stack_mb) * 1024 * 1024,
        "procLimit": int(proc_limit),
        "copyIn": dict(copy_in or {}),
        "copyOut": list(copy_out),
        "copyOutMax": int(copy_out_max),
        "copyOutTruncate": False,
    }
    if files:
        # 更精细的文件表（比如要单独给 stdin 挂文件）时用调用方给的
        cmd["files"] = files
    if cached:
        cmd["copyOutCached"] = list(cached)
    resp = _post("/run", {"cmd": [cmd]})
    rows = resp if isinstance(resp, list) else (resp or {}).get("results") or []
    if not rows:
        raise GoJudgeError(f"沙箱没返回结果：{resp!r}")
    row = rows[0]
    # stdout/stderr 统一成 bytes 方便比较（沙箱回的是文本，但内容可能不是 UTF-8）
    fl = row.get("files") or {}
    if isinstance(fl, dict):
        row["files"] = {k: (v if isinstance(v, bytes) else str(v).encode("utf-8"))
                        for k, v in fl.items()}
    return row


def selftest() -> dict:
    """自检：真编译、真跑、真触发 TLE/MLE，确认限额生效。

    为什么值得做：限额字段名写错时沙箱**不报错**，程序会没限额地跑飞
    （死循环就一直占着 CPU）。这一条把"限额到底管不管用"钉死。
    """
    out = {"ok": False, "checks": [], "error": ""}
    src = ("#include <cstdio>\nint main(){int a,b;if(scanf(\"%d %d\",&a,&b)!=2)return 1;"
           "printf(\"%d\\n\",a+b);return 0;}\n")
    try:
        # 源码内联传（`{"content": 原文}`）；编译产物用 copyOutCached 留在仓库里，
        # 后面每个测试点直接引用它的 fileId —— 这就是判题引擎省掉重复编译的那一招
        r = run(["/usr/bin/g++", "-O2", "-std=c++14", "main.cpp", "-o", "main"],
                copy_in={"main.cpp": {"content": src}},
                cpu_ms=20000, clock_ms=60000, memory_mb=2048, cached=["main"])
        out["checks"].append(("编译", r.get("status") == "Accepted",
                              str(r.get("status")) + " " +
                              (r.get("files", {}).get("stderr", b"")[:200].decode(
                                  "utf-8", "replace"))))
        bin_id = (r.get("fileIds") or {}).get("main")
        if not bin_id:
            out["error"] = "编译没产出可执行文件"
            return out
        r = run(["main"], copy_in={"main": {"fileId": bin_id}}, stdin=b"1 2\n",
                cpu_ms=2000, memory_mb=256)
        got = (r.get("files") or {}).get("stdout", b"")
        out["checks"].append(("运行", r.get("status") == "Accepted" and got == b"3\n",
                              f"{r.get('status')} out={got!r}"))
        # 死循环：CPU 限额 1 秒，必须被拦
        r = run(["/usr/bin/g++", "-O2", "sp.cpp", "-o", "sp"],
                copy_in={"sp.cpp": {"content": "int main(){for(;;);}\n"}},
                cpu_ms=20000, memory_mb=2048, cached=["sp"])
        sid = (r.get("fileIds") or {}).get("sp")
        r = run(["sp"], copy_in={"sp": {"fileId": sid}}, cpu_ms=1000, memory_mb=256)
        out["checks"].append(("CPU 限额", r.get("status") == "Time Limit Exceeded",
                              str(r.get("status"))))
        # 吃内存：128MB 限额，必须被拦
        ml_src = ("#include <cstdlib>\n#include <cstring>\nint main(){for(int i=0;i<64;i++)"
                  "{char*p=(char*)malloc(32*1024*1024);if(!p)return 0;"
                  "memset(p,1,32*1024*1024);}return 0;}\n")
        r = run(["/usr/bin/g++", "-O2", "ml.cpp", "-o", "ml"],
                copy_in={"ml.cpp": {"content": ml_src}}, cpu_ms=20000, memory_mb=2048,
                cached=["ml"])
        mid = (r.get("fileIds") or {}).get("ml")
        r = run(["ml"], copy_in={"ml": {"fileId": mid}}, cpu_ms=5000, memory_mb=128)
        out["checks"].append(("内存限额",
                              r.get("status") in ("Memory Limit Exceeded", "Signalled"),
                              str(r.get("status"))))
        # 安全底线：沙箱里读不到宿主机的考试服务目录
        r = run(["/bin/sh", "-c", "cat /root/csp-exam/data/admin_key.txt"],
                cpu_ms=2000, memory_mb=128)
        leaked = bool((r.get("files") or {}).get("stdout", b"").strip())
        out["checks"].append(("沙箱隔离", not leaked, str(r.get("status"))))
        for fid2 in (bin_id, sid, mid):
            drop(fid2)
    except GoJudgeError as e:
        out["error"] = str(e)
        return out
    out["ok"] = all(ok for _n, ok, _d in out["checks"])
    return out


if __name__ == "__main__":          # 手工体检：python3 -m csp_exam.core.gojudge
    import sys
    p = probe()
    print("沙箱：", json.dumps(p, ensure_ascii=False))
    r = selftest()
    for name, ok, detail in r["checks"]:
        print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("结论：", "正常" if r["ok"] else ("异常：" + str(r["error"])))
    sys.exit(0 if r["ok"] else 1)
