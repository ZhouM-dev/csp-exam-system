#!/bin/bash
# 清空考试数据（删掉某场比赛，或整体重来）
# 注意：不会删除评测站（Hydro）上的题目；学生账号会保留（考号池复用，不重复发号）
#
#   bash reset_exam.sh            # 列出所有比赛，交互式选一场删除
#   bash reset_exam.sh c2         # 删掉 c2 这一场（名单、题目、成绩、提交代码全删）
#   bash reset_exam.sh --all      # 删掉所有比赛 + 考号池（完全从头开始）
set -u
DATA=/root/csp-exam/data
IDX=$DATA/contests.json

list_contests() {
  python3 -c "
import json, io, os
cs = json.load(io.open('$IDX', encoding='utf-8')) if os.path.exists('$IDX') else []
for c in cs:
    d = '$DATA/contests/%s' % c['id']
    e = json.load(io.open(d + '/exam.json', encoding='utf-8')) if os.path.exists(d + '/exam.json') else {}
    r = json.load(io.open(d + '/roster.json', encoding='utf-8')) if os.path.exists(d + '/roster.json') else []
    res = json.load(io.open(d + '/results.json', encoding='utf-8')) if os.path.exists(d + '/results.json') else {}
    print('  %-4s %-20s %-4s 名单 %d 人，题目 %d 道，答卷 %d 份' % (
        c['id'], c['title'], c['rule'], len(r), len(e.get('problems', [])), len(res)))
"
}

echo "现有比赛："
list_contests
echo

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  read -r -p "要删除哪一场？输入比赛 id（或 --all 全部清空）：" TARGET
fi

case "$TARGET" in
  --all)
    read -r -p "确认清空【所有比赛 + 考号池】？输入 yes 继续：" ans
    [ "$ans" = "yes" ] || { echo "已取消"; exit 0; }
    systemctl stop csp-exam
    rm -rf "$DATA/contests" "$IDX" "$DATA/students.json"
    # 早期单场比赛的遗留文件也一并删掉
    rm -f "$DATA/exam.json" "$DATA/roster.json" "$DATA/results.json"
    rm -rf "$DATA/uploads"
    systemctl start csp-exam
    echo "已清空（管理密钥保留在 $DATA/admin_key.txt）"
    ;;
  "")
    echo "已取消"; exit 0 ;;
  *)
    python3 -c "
import json, io, os, shutil
p = '$IDX'
cs = json.load(io.open(p, encoding='utf-8')) if os.path.exists(p) else []
hit = [c for c in cs if c['id'] == '$TARGET']
if not hit:
    raise SystemExit('  没有编号为 $TARGET 的比赛')
shutil.rmtree('$DATA/contests/$TARGET', ignore_errors=True)
json.dump([c for c in cs if c['id'] != '$TARGET'], io.open(p, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
print('  已删除 %s（%s）' % ('$TARGET', hit[0]['title']))
"
    ;;
esac

sleep 1
systemctl is-active csp-exam | sed 's/^/服务状态: /'
echo
echo "剩余比赛："
list_contests
