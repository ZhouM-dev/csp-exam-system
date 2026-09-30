#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一遍包内的全部自测（纯逻辑、不需要 docker、可以随时跑）。

    python3 tools/run_selftests.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(os.path.dirname(HERE), "tests")


def main() -> int:
    tests = sorted(f for f in os.listdir(TESTS) if f.startswith("selftest_") and f.endswith(".py"))
    if not tests:
        print("没有找到自测脚本：", TESTS)
        return 1
    ok = fail = total = 0
    for name in tests:
        p = subprocess.run([sys.executable, name], cwd=TESTS,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        nums = [l for l in out.splitlines() if "结果" in l]
        line = nums[-1].strip() if nums else ""
        if p.returncode == 0:
            ok += 1
            print("  [通过] %-28s %s" % (name, line))
            m = re.search(r"(\d+)\s*项通过", line)
            if m:
                total += int(m.group(1))
        else:
            fail += 1
            print("  [失败] %-28s" % name)
            print("\n".join("      " + l for l in out.strip().splitlines()[-6:]))
    print()
    print("自测：%d 套通过，%d 套失败，共 %d 项断言" % (ok, fail, total))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
