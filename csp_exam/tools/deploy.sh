#!/usr/bin/env bash
# 一条命令部署考试服务到服务器（比逐个 scp 稳，不会再漏文件）。
#
#   bash csp_exam/tools/deploy.sh [服务器]       默认 admin@<你的服务器IP>
#
# 做的事：同步源码/文档/验收脚本 → 服务端导入检查 → 重启 → 全路由冒烟 → 包内自测。
# 数据（data/）与日志（logs/）不会被覆盖。
#
# 目录约定：本脚本在 <树根>/csp_exam/tools/ 下，<树根> 就是要同步上去的那棵树
# （本地工作副本 ↔ 服务器 /root/csp-exam，两边同构）。
set -euo pipefail

HOST="${1:-admin@<你的服务器IP>}"
# 登录用的是普通用户 admin（root@ 直登不通），密钥默认这个；换机加 CSP_SSH_KEY 环境变量
KEY="${CSP_SSH_KEY:-$HOME/.ssh/<你的密钥名>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # → 树根
REMOTE=/root/csp-exam
SSH="ssh -i $KEY -o BatchMode=yes $HOST"

[ -f "$ROOT/run.py" ] || { echo "在 $ROOT 下找不到 run.py，脚本位置不对？"; exit 1; }

echo "=== 1. 同步源码到 $HOST:$REMOTE ==="
# --no-same-owner：别把本地（Windows/MSYS）的 uid 带到服务器上，落盘一律 root
tar czf - -C "$ROOT" run.py csp_exam | $SSH "sudo tar xzf - --no-same-owner -C $REMOTE"
$SSH "sudo find $REMOTE/csp_exam -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true"
# 「能不能 import」用 stdin 喂脚本，省得跟 sudo/单引号/双引号三层转义打架
$SSH "sudo env PYTHONPATH=$REMOTE python3 -" <<'PY'
import csp_exam, csp_exam.compat
print("  服务端导入 OK")
PY

echo
echo "=== 2. 同步文档与验收脚本 ==="
# 本地的「项目索引」在开发机上叫 README.md、在这份工作副本里叫 项目索引.md，两个名字都认
IDX=""
for cand in 项目索引.md README.md; do
  [ -f "$ROOT/$cand" ] && { IDX="$cand"; break; }
done
SYNC="docs tests"
[ -n "$IDX" ] && SYNC="$SYNC $IDX"
tar czf - -C "$ROOT" $SYNC | $SSH "sudo tar xzf - --no-same-owner -C $REMOTE"
if [ -n "$IDX" ] && [ "$IDX" != "项目索引.md" ]; then
  $SSH "sudo mv -f $REMOTE/$IDX $REMOTE/项目索引.md"
fi
$SSH "sudo ls $REMOTE/docs | sed 's/^/  docs\//'"

echo
echo "=== 3. 重启服务 ==="
$SSH "sudo systemctl restart csp-exam && sleep 2 && systemctl is-active csp-exam" | sed 's/^/  /'

echo
echo "=== 4. 全路由冒烟 ==="
$SSH "sudo bash $REMOTE/tests/_smoke_pages.sh" | tail -3

echo
echo "=== 5. 包内自测 ==="
$SSH "sudo env PYTHONPATH=$REMOTE python3 $REMOTE/csp_exam/tools/run_selftests.py"

echo
echo "部署完成。要跑完整验收： ssh -i $KEY $HOST 'sudo bash $REMOTE/tests/_regress_all.sh'"
