#!/bin/bash
# HTTP 题单导入接口（`code` 字段 = 默认英文名）—— 包装 Python 实现。
#
#   bash tests/_e2e_httpimport.sh
#
# 需要读 /root/csp-exam/data 下的管理密钥与编号登记表，所以不是 root 时自动 sudo。
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" = "0" ]; then
  exec python3 "$DIR/_e2e_httpimport.py"
fi
exec sudo python3 "$DIR/_e2e_httpimport.py"
