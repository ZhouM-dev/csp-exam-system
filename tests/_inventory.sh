#!/bin/bash
# 盘点服务器上"可能冗余"的东西
set -u
echo "=== 1. 所有 systemd 服务（本项目的）==="
systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -iE "oijudge|csp|hydro|judge" | sed 's/^/  /'
echo "  --- 已 enable 的自定义 unit ---"
ls /etc/systemd/system/*.service 2>/dev/null | sed 's/^/  /'

echo
echo "=== 2. 考试服务数据目录（有哪些文件/目录）==="
ls -la /root/csp-exam/data/ | sed 's/^/  /'
echo "  --- 各比赛大小 ---"
du -sh /root/csp-exam/data/contests/* 2>/dev/null | sed 's/^/  /'
echo "  --- 大样例存档 ---"
ls -la /root/csp-exam/data/samples/ 2>/dev/null | sed 's/^/  /'

echo
echo "=== 3. 我留下的临时/测试残留 ==="
echo "  --- Hydro 导入临时目录 ---"
du -sh /root/hydro/data/backend/import/* 2>/dev/null | sed 's/^/  /'
echo "  --- 考试服务的测试临时目录 ---"
du -sh /root/csp-exam/tests/tmp 2>/dev/null | sed 's/^/  /'
ls /root/csp-exam/tests/tmp 2>/dev/null | head -8 | sed 's/^/    /'
echo "  --- /root 下的散落文件 ---"
ls -la /root/*.txt /root/*.sh /root/*.json 2>/dev/null | sed 's/^/  /'
echo "  --- 探针账号（评测站里我建的测试账号）---"
docker compose -f /root/hydro/docker-compose.yml exec -T oj-mongo mongosh hydro --quiet --eval \
  'print(JSON.stringify(db.user.find({uname:/probe|t-probe|c9-|^c[0-9]*-/},{uname:1,_id:1}).toArray()))' 2>/dev/null | sed 's/^/  /'

echo
echo "=== 4. 评测站上的题目（有没有测试残留）==="
docker compose -f /root/hydro/docker-compose.yml exec -T oj-mongo mongosh hydro --quiet --eval \
  'print(db.document.find({docType:10},{pid:1,title:1}).sort({docId:1}).toArray().map(d=>d.pid+" \""+d.title+"\"").join(" | "))' 2>/dev/null | sed 's/^/  /'

echo
echo "=== 5. 磁盘占用 ==="
du -sh /root/csp-exam /root/csp-ui /root/hydro/data 2>/dev/null | sed 's/^/  /'
df -h / | tail -1 | sed 's/^/  /'
