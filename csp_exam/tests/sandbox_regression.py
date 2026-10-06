"""受控程序在 go-judge 中验收；所有测试题和成绩只写临时目录。"""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from csp_exam.core import gojudge, judge_profile, judgelocal, store


def main():
    checks = []
    def check(name, ok, details=""):
        checks.append({"name": name, "ok": bool(ok), "details": details})
    with tempfile.TemporaryDirectory(prefix="csp-sandbox-regression-") as tmp, \
            patch.object(store, "DATA_DIR", tmp), \
            patch.object(judgelocal, "PROBLEMS_DIR", str(Path(tmp) / "problems")):
        environment = gojudge.ensure_ready()
        check("官方镜像编译器 / cgroup / seccomp", bool(environment), environment)
        basic = judgelocal.selftest()
        for name, r in basic["checks"]:
            check(name, basic["ok"], {"status": r["status"], "score": r.get("score"), "note": r.get("note")})
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b"ok\n"}])
        def judge(source, *, memory=512, time=1000):
            return judgelocal.judge_source("P", source, ".cpp", code="candy", full=100,
                                           memory_mb=memory, time_ms=time, io_mode="file")
        stack = '#include <cstdio>\nint main(){volatile char a[16*1024*1024]; for(int i=0;i<sizeof(a);i++)a[i]=i; FILE*f=fopen("candy.out","w");fprintf(f,"ok\\n");fclose(f);return a[0];}'
        r = judge(stack)
        check("16 MiB 栈在 512 MiB 限额内通过", r["status"] == 1, {"status": r["status"], "memory_kb": r.get("memory")})
        memory = '#include <cstdio>\n#include <cstdlib>\nint main(){volatile char*p=(char*)malloc(64*1024*1024);if(!p)return 1;for(int i=0;i<64*1024*1024;i++)p[i]=i;FILE*f=fopen("candy.out","w");fprintf(f,"ok\\n");fclose(f);return p[0];}'
        r = judge(memory, memory=32)
        check("触碰 64 MiB 堆受 32 MiB 内存限制", r["status"] == 4, {"status": r["status"], "memory_kb": r.get("memory")})
        r = judge('int main(){for(;;);}', time=200)
        check("200 ms CPU 超时", r["status"] == 3, {"status": r["status"], "time_ms": r.get("time")})
        nofile = '#include <cstdio>\nint main(){printf("ok\\n");}'
        r = judge(nofile)
        check("正确标准输出不能冒充 CSP 答案文件", r["status"] == 2 and r["score"] == 0)
        src = '#include <cstdio>\nint main(){FILE*f=fopen("candy.out","wb");fputc(255,f);fclose(f);return 0;}'
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b"\xfe"}])
        r = judge(src)
        check("不同非法 UTF-8 字节必须 WA", r["status"] == 2)
        judgelocal.store_problem("P", [{"name": "1", "in": b"\xff\x00\xfe\n", "out": b"\xff\x00\xfe\n"}])
        r = judge('#include <cstdio>\nint main(){FILE*i=fopen("candy.in","rb"),*o=fopen("candy.out","wb");int c;while((c=fgetc(i))!=EOF)fputc(c,o);fclose(o);return 0;}')
        check("输入和文件输出按原始字节传递", r["status"] == 1)
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b"ok\n"}])
        src = '#include <cstdio>\nint main(){FILE*f=fopen("candy.out","w");fprintf(f,"ok\\n");fclose(f);}'
        judge_profile.save(1.5)
        r = judge(src)
        check("倍率保存原时限 / 有效时限 / 实际耗时", r.get("official_time_ms") == 1000 and
              r.get("effective_time_ms") == 1500 and r.get("time_scale") == 1.5)
        # 编译产物由独立受控编译检查：静态 ELF 不得包含 INTERP。
        sid, bid = gojudge.prepare(src.encode()), ""
        try:
            c = gojudge.run(["/usr/bin/g++", "-O2", "-std=c++14", "-static", "candy.cpp", "-o", "candy"],
                            copy_in={"candy.cpp": {"fileId": sid}}, cpu_ms=30000, memory_mb=2048, cached=["candy"])
            bid = (c.get("fileIds") or {}).get("candy", "")
            elf = gojudge.run(["/usr/bin/readelf", "-l", "candy"], copy_in={"candy": {"fileId": bid}})
            check("静态链接 ELF", bid and b"INTERP" not in elf["files"]["stdout"])
        finally:
            gojudge.drop(sid)
            gojudge.drop(bid)
    print(json.dumps({"ok": all(c["ok"] for c in checks), "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
