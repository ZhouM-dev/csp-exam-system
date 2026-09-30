#!/usr/bin/env python3
"""考试服务入口。systemd 用这个（也可以 python3 -m csp_exam）。

    python3 run.py            # 前台运行，端口 8080（CSP_EXAM_PORT 可改）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from csp_exam.web.server import main   # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
