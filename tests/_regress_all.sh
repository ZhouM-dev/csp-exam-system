#!/usr/bin/env bash
# --quick: 逻辑及隔离管理 HTTP；--live: 增加真实沙箱与交卷重测。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="${1:---quick}"
case "$MODE" in --quick|--live) ;; *) echo "用法：$0 [--quick|--live]" >&2; exit 2;; esac
python3 csp_exam/tools/run_selftests.py
if [[ "$MODE" == --live ]]; then
  python3 -m csp_exam.tests.http_management_regression --live
  python3 -m csp_exam.tests.http_judge_regression
  python3 -m csp_exam.tests.sandbox_regression
else
  python3 -m csp_exam.tests.http_management_regression
fi
