#!/bin/bash
# 恢复被我覆盖掉的题目 G01（原题包还在导入目录里），并核对内容
set -u
cd /root/csp-exam || exit 1
P=/root/hydro/data/backend/import/G01
echo "=== 1. import 目录里的 G01 题包是什么内容 ==="
ls "$P" 2>/dev/null | sed 's/^/  /'
echo "  --- 题面前 6 行 ---"
head -6 "$P/题目.md" 2>/dev/null | sed 's/^/  /'
echo "  --- 数据组数 ---"
ls "$P/data" 2>/dev/null | wc -l | sed 's/^/  /'
ls "$P/data" 2>/dev/null | head -4 | sed 's/^/    /'
echo "  --- 标程前 3 行 ---"
head -3 "$P/标程.cpp" 2>/dev/null | sed 's/^/  /'
echo "  --- 题包时间 ---"
stat -c '  %y' "$P" 2>/dev/null

echo
echo "=== 2. 如果确实是原题，就重新导入 ==="
python3 - <<'PY'
import import_problemset as ip
pids = ip.hydro_problem_pids()
print("  当前站点有 G01 吗：", "G01" in pids)
if "G01" not in pids:
    ok, err = ip.hydro_import("G01", ip._container_path(ip.HOST_IMPORT_DIR))
    print("  重新导入：", ok, err[:120] if err else "")
else:
    print("  已经有 G01，不需要恢复")
PY

echo
echo "=== 3. 核对恢复结果 ==="
python3 - <<'PY'
import subprocess, json
js = ('const d=db.document.findOne({docType:10,pid:"G01"},{docId:1,title:1,config:1}); '
      'if(!d){print("NOT FOUND")} else {'
      'print(JSON.stringify({docId:d.docId,title:d.title,config:d.config,'
      'cases:db.document.findOne({docType:10,pid:"G01"},{data:1}).data.map(x=>x.name)}))}')
p = subprocess.run(["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-mongo",
                    "mongosh","hydro","--quiet","--eval",js], cwd="/root/hydro",
                   capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
out = (p.stdout or "").strip()
print("  ", out[-300:])
PY
python3 -c "
import store, hydro_client as h
store.save_catalog(h.list_problems())
print('  题库缓存里有 G01：', any(str(i['pid']) == 'G01' for i in store.load_catalog().get('items') or []))
"
