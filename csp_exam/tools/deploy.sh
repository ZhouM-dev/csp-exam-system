#!/usr/bin/env bash
# 同步源码/文档/测试，保留 data 与 logs。使用现有 SSH 配置，密钥可选。
set -euo pipefail
HOST="${1:?用法：bash csp_exam/tools/deploy.sh admin@服务器}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH=(ssh -o BatchMode=yes)
[[ -z "${CSP_SSH_KEY:-}" ]] || SSH+=(-i "$CSP_SSH_KEY")
REMOTE=/root/csp-exam
STAGE="$("${SSH[@]}" "$HOST" 'mktemp -d /tmp/csp-deploy.XXXXXX')"
[[ "$STAGE" == /tmp/csp-deploy.* ]] || { echo "暂存路径不正确" >&2; exit 1; }
cleanup_remote() { "${SSH[@]}" "$HOST" "sudo -n rm -rf -- $STAGE"; }
trap cleanup_remote EXIT
bash "$ROOT/tests/_regress_all.sh" --quick
tar czf - --exclude=__pycache__ --exclude='*.pyc' -C "$ROOT" run.py csp_exam docs tests tasks README.md 项目索引.md 整改清单.md | "${SSH[@]}" "$HOST" "tar xzf - -C $STAGE"
"${SSH[@]}" "$HOST" "cd $STAGE && python3 csp_exam/tools/run_selftests.py"
"${SSH[@]}" "$HOST" "sudo -n python3 - $STAGE" <<'PY'
import pathlib,shutil,sys,tarfile,time
root=pathlib.Path('/root/csp-exam');stage=pathlib.Path(sys.argv[1])
assert str(stage.resolve()).startswith('/tmp/csp-deploy.')
sys.path.insert(0,str(root))
from csp_exam.core import store
assert not any(r.get('judging') for c in store.list_contests() for r in store.load_results(c['id']).values()), '仍有在途评测，请稍后部署'
backup=pathlib.Path('/root/csp-history-backup-20261007-012536');backup.mkdir(exist_ok=True)
with tarfile.open(backup/'latest-source-rollback.tgz','w:gz') as tar:
 for name in ('run.py','csp_exam','docs','tests','tasks','README.md','项目索引.md','整改清单.md'):
  if (root/name).exists():tar.add(root/name,arcname=name,filter=lambda m:None if '__pycache__' in m.name or m.name == 'tests/tmp' or m.name.startswith('tests/tmp/') else m)
import subprocess
subprocess.run(['systemctl','stop','csp-exam'],check=True)
try:
 for name in ('csp_exam','docs','tests','tasks'):
  if (root/name).exists():shutil.rmtree(root/name)
  shutil.copytree(stage/name,root/name)
 for name in ('run.py','README.md','项目索引.md','整改清单.md'):shutil.copy2(stage/name,root/name)
finally:subprocess.run(['systemctl','start','csp-exam'],check=True)
PY
"${SSH[@]}" "$HOST" "sudo -n bash -c 'cd $REMOTE && bash tests/_smoke_pages.sh'"
echo '部署完成，业务数据保持原样。'
