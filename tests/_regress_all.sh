#!/bin/bash
# 全量验收（冒烟 + 自测 + 端到端）。默认全跑，也可以只跑需要的部分：
#
#   bash tests/_regress_all.sh                 # 冒烟 + 自测 + 7 套端到端（3–8 分钟）
#   bash tests/_regress_all.sh --quick         # 只跑冒烟 + 自测（约 40 秒）
#   bash tests/_regress_all.sh rules roster    # 只跑指定的端到端（另加冒烟 + 自测）
#
# 挑套件跑是"省时间"的正道：改学生端看题面就跑 rules；改名册/考号跑 roster；
# 改建题目跑 mkproblem folder；改判分/数据就全跑。
set -u
cd /root/csp-exam || exit 1

ALL="_e2e_rules _e2e_new _e2e_roster _e2e_tree _e2e_mkproblem _e2e_problemdetail _e2e_reupload _e2e_folder _e2e_httpimport"
QUICK=0
PICKED=""
for a in "$@"; do
  case "$a" in
    --quick|-q) QUICK=1 ;;
    all)        PICKED="$ALL" ;;
    *)          case "$a" in _e2e_*) ;; *) a="_e2e_$a" ;; esac
                case "$a" in *.sh) ;; *) a="$a.sh" ;; esac
                PICKED="$PICKED $a" ;;
  esac
done
[ -n "$PICKED" ] || PICKED="$ALL"

echo "=== 1. 全路由冒烟 ==="
bash tests/_smoke_pages.sh 2>&1 | grep -E "FAIL|结果："

echo
echo "=== 2. 本地自测 ==="
python3 csp_exam/tools/run_selftests.py

if [ "$QUICK" = "1" ]; then
  echo
  echo "（--quick：跳过端到端。要跑全部：bash tests/_regress_all.sh）"
  exit 0
fi

echo "=== 3. 端到端验收 ==="
for S in $PICKED; do
  case "$S" in *.sh) ;; *) S="$S.sh" ;; esac
  [ -f "tests/$S" ] || { printf '  %-16s 没有这个套件\n' "$S"; continue; }
  printf '  %-16s ' "$S"
  out=$(bash "tests/$S" 2>&1)
  f=$(printf '%s\n' "$out" | grep -cE '^[[:space:]]+\[FAIL\]')
  p=$(printf '%s\n' "$out" | grep -cE '^[[:space:]]+\[PASS\]')
  if [ "$f" = "0" ]; then
    echo "全部通过（$p 项）"
  else
    echo "失败 $f 项"
    printf '%s\n' "$out" | grep -E '^[[:space:]]+\[FAIL\]' | head -6 | sed 's/^/      /'
  fi
done
