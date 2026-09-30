#!/bin/bash
# 部署后验证：题面能取到干净的 Markdown（不是原始 JSON）
set -u
cd /root/csp-exam || exit 1
rm -rf data/statements
python3 - <<'PY'
import hydro_client as h
for pid, title in (("P1001", "A+B Problem"), ("P1002", "求和"), ("P1003", "判断素数")):
    t = h.problem_statement(pid, "data/statements")
    head = t.strip().splitlines()[0] if t.strip() else "(空)"
    print("  %-6s %-12s %4d 字  首行：%s" % (pid, title, len(t), head[:50]))
    if t.strip().startswith("{"):
        print("      !! 还是 JSON 原文")
PY
echo
echo "=== 缓存目录 ==="
ls -l data/statements/ | sed 's/^/  /'
echo
echo "=== 学生页面渲染检查（取题面页的正文片段）==="
J=/root/csp-exam/tests/tmp/t.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST http://127.0.0.1:8080/enter \
  --data-urlencode "c=c1" --data-urlencode "kaohao=GD-0001" --max-time 20
curl -s -b $J "http://127.0.0.1:8080/problem?c=c1&p=2" -o /tmp/stmt.html --max-time 20
python3 - <<'PY'
import re, html
p = open('/tmp/stmt.html', encoding='utf-8').read()
body = re.search(r'<div class="card stmt">(.*?)</div>', p, re.S)
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body.group(1))) if body else '(没找到题面区块)'
print('  题面正文：', text[:160])
print('  含原始 JSON 吗：', '{"zh"' in p)
print('  有没有渲染成标签（h2/pre/table）：',
      any(t in p for t in ('<h2>', '<pre class="stmt-code">', '<table>')))
PY
