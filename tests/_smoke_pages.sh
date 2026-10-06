#!/usr/bin/env bash
# 实际服务只读冒烟。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 csp_exam/tools/smoke_pages.py
