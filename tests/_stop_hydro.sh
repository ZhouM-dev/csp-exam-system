#!/bin/bash
# 停掉 Hydro（判题已经完全在本地 go-judge 上跑）
#
# **用 stop 不用 down**：容器和数据都留着，想回退就 `docker compose start`，
# 一行命令的事。守卫服务（hydro-judge-guard）是给 Hydro 判分机补判题会话的，
# Hydro 停了它没意义，一并停掉（disable，重启也不会自己起来）。
set -u
echo "=== 停之前 ==="
sudo docker ps --format '  {{.Names}}\t{{.Status}}'
systemctl is-active csp-exam | sed 's/^/  csp-exam: /'

echo
echo "=== 停 Hydro 容器 ==="
(cd /root/hydro && sudo docker compose stop 2>&1 | tail -6) || echo "  （compose stop 出错，看上面）"
echo "=== 停判分守卫 ==="
sudo systemctl disable --now hydro-judge-guard 2>&1 | tail -2 || true

echo
echo "=== 停之后 ==="
sudo docker ps --format '  {{.Names}}\t{{.Status}}' || true
echo "  hydro-judge-guard: $(systemctl is-active hydro-judge-guard 2>/dev/null || echo inactive)"
echo "  csp-exam:         $(systemctl is-active csp-exam)"

echo
echo "=== 考试服务体检（判题后端 + 题目数据齐不齐）==="
sudo env PYTHONPATH=/root/csp-exam PYTHONIOENCODING=utf-8 python3 - <<'PY'
from csp_exam.core import localoj
h = localoj.health()
print("  判题沙箱：%s（go-judge %s）" % ("正常" if h["judge_ok"] else "不可用", h["judge_version"]))
if h["judge_error"]:
    print("  问题：", h["judge_error"])
print("  题目 %d 道，其中没数据的：%s" % (h["problems"], h["no_data"] or "无"))
PY

echo
echo "=== 学生端/管理端页面还能开吗（最常用的两个）==="
KEY=$(sudo cat /root/csp-exam/data/admin_key.txt)
curl -s -o /dev/null -w "  学生入口 /enter -> %{http_code}\n" --max-time 20 "http://127.0.0.1:8080/enter"
curl -s -o /dev/null -w "  题目列表 /admin/problems -> %{http_code}\n" --max-time 30 \
  "http://127.0.0.1:8080/admin/problems?key=$KEY"
echo
echo "回退办法：cd /root/hydro && sudo docker compose start ；再把这轮的代码换回去。"
