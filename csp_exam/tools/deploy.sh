#!/usr/bin/env bash
# Build only: no SSH, installation, service restart, or production changes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${1:-}" != "--build" ]]; then
  echo '用法：bash csp_exam/tools/deploy.sh --build /绝对路径/csp-exam.tgz' >&2
  echo '部署步骤见 docs/平台部署方案.md。旧脚本指向另一台生产机器，已停用。' >&2
  exit 2
fi
python3 "$ROOT/csp_exam/tools/build_release.py" "${2:?请指定输出文件}"
