#!/bin/bash
# 1) 确认 G01 标题  2) 服务器端清理：旧数据归档、散落脚本、探针账号、无用题目、导入缓存
set -u
cd /root/csp-exam || exit 1

echo "=== 1. G01 恢复情况 ==="
python3 -c "
import store
d = [i for i in store.load_catalog().get('items') or [] if str(i['pid']) == 'G01']
print('  ', d)
print('  c1 是否用到 G01：', any(str(p.get('pid')) == 'G01' for p in store.load_exam('c1').get('problems', [])))
"

echo
echo "=== 2. 旧单场比赛的遗留文件归档（不删，挪到 data/_backup_旧单场/）==="
mkdir -p data/_backup_旧单场
for f in exam.json roster.json results.json students.json; do
  if [ -f "data/$f" ]; then
    mv "data/$f" "data/_backup_旧单场/" && echo "  归档 data/$f"
  fi
done
if [ -e "data/contests/exam.json" ]; then
  echo "  发现异常项 data/contests/exam.json，查看："
  ls -la data/contests/ | sed 's/^/    /'
  mv "data/contests/exam.json" "data/_backup_旧单场/contests-exam.json.bak" && echo "  已挪走"
fi
echo "  现在 data/：$(ls data/ | tr '\n' ' ')"

echo
echo "=== 3. 清掉 /root 下我留下的临时脚本 ==="
rm -f /root/deploy.sh /root/_deploy_multi.sh && echo "  已删除"

echo
echo "=== 4. 清掉测试临时目录 ==="
rm -rf /root/csp-exam/tests/tmp && mkdir -p /root/csp-exam/tests/tmp && echo "  已清空 tests/tmp"

echo
echo "=== 5. 删除评测站上的探针账号（我测试建的）==="
python3 - <<'PY'
import subprocess, json
names = ["probe-12508", "t-probe-acc", "c9-GD-0001", "c9-GD-0002",
         "c4-GD-0001", "c4-GD-0003", "c4-GD-0005", "c5-GD-0002"]
js = ('const us=db.user.find({uname:{$in:%s}},{_id:1,uname:1}).toArray();'
      'print(JSON.stringify(us))' % json.dumps(names))
p = subprocess.run(["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-mongo",
                    "mongosh","hydro","--quiet","--eval",js], cwd="/root/hydro",
                   capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
line = [l for l in (p.stdout or "").splitlines() if l.startswith("[")]
us = json.loads(line[0]) if line else []
if not us:
    print("  没有找到探针账号（可能已删）")
for u in us:
    uid, uname = u["_id"], u["uname"]
    cmd = ["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-backend",
           "hydrooj","cli","user","delete", str(uid)]
    r = subprocess.run(cmd, cwd="/root/hydro", capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print("  删除账号 %-14s uid=%-5s -> %s" % (uname, uid, "OK" if ok else (r.stdout or r.stderr)[-80:]))
PY

echo
echo "=== 6. 删掉 pid 为空的测试题 ==="
python3 - <<'PY'
import subprocess, json
js = ('const d=db.document.find({docType:10,$or:[{pid:null},{pid:""},{pid:{$exists:false}}]},{docId:1,title:1}).toArray();'
      'print(JSON.stringify(d))')
p = subprocess.run(["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-mongo",
                    "mongosh","hydro","--quiet","--eval",js], cwd="/root/hydro",
                   capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
line = [l for l in (p.stdout or "").splitlines() if l.startswith("[")]
docs = json.loads(line[0]) if line else []
for d in docs:
    print("  找到无标识题目：docId=%s title=%s" % (d.get("docId"), d.get("title")))
    r = subprocess.run(["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-backend",
                        "hydrooj","cli","problem","delete", "system", str(d.get("docId"))],
                       cwd="/root/hydro", capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    print("   删除:", "OK" if r.returncode == 0 else (r.stdout or r.stderr)[-100:])
if not docs:
    print("  没有无标识题目")
PY

echo
echo "=== 7. 清空 Hydro 导入缓存（题目数据已经进站点，这堆是约 450MB 的原始包）==="
BEFORE=$(du -sh /root/hydro/data/backend/import 2>/dev/null | cut -f1)
rm -rf /root/hydro/data/backend/import/*
python3 -c "import os; os.makedirs('/root/hydro/data/backend/import', exist_ok=True)"
echo "  已清空（原 $BEFORE）"

echo
echo "=== 8. 结果 ==="
du -sh /root/csp-exam /root/hydro/data 2>/dev/null | sed 's/^/  /'
df -h / | tail -1 | sed 's/^/  /'
systemctl is-active csp-exam | sed 's/^/  csp-exam: /'
