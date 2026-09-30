#!/bin/bash
# 查有没有卡住的判题（judging=True 或题目状态还是"进行中"）
set -u
cd /root/csp-exam || exit 1
python3 - <<'PY'
import glob
import json

busy = 0
for f in sorted(glob.glob('data/contests/*/results.json')):
    r = json.load(open(f, encoding='utf-8'))
    for k, v in r.items():
        if v.get('judging'):
            busy += 1
            print('  judging 卡住：%s %s 提交于 %s' % (f, k, v.get('submitted_at')))
        for pn, pv in (v.get('problems') or {}).items():
            if pv.get('status') in (0, 20, 21, 22):
                busy += 1
                print('  题目未完成：%s %s %s -> %s' % (f, k, pn, pv.get('status_text')))
print('  合计未完成：%d' % busy)
PY
echo
echo "=== 判分机在忙吗 / 队列里有什么 ==="
docker compose -f /root/hydro/docker-compose.yml logs --tail=8 oj-judge 2>&1 | tail -6 | sed 's/^/  /'
echo
echo "=== 最近的提交（考试服务日志）==="
tail -12 /root/csp-exam/logs/exam.log | sed 's/^/  /'
