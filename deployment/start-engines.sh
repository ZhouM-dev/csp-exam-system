set -eu
id csp-exam >/dev/null 2>&1 || useradd --system --home-dir /var/lib/csp-exam --shell /usr/sbin/nologin csp-exam
install -d -m 0700 -o csp-exam -g csp-exam /var/lib/csp-exam /var/lib/csp-exam/problems /var/lib/csp-exam/hydro-work /var/log/csp-exam
install -d -m 0700 /var/lib/csp-go-judge/cache
if [ ! -f /etc/csp-hydro.env ]; then
    python3 - <<'PY'
import secrets,os
fd=os.open('/etc/csp-hydro.env',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:
    f.write('CSP_HYDRO_TOKEN='+secrets.token_hex(32)+'\n')
    f.write('CSP_EXAM_DATA_DIR=/var/lib/csp-exam\n')
PY
fi
if ! docker container inspect csp-go-judge >/dev/null 2>&1; then
    docker run -d --name csp-go-judge --restart unless-stopped --privileged --cgroupns private \
      --cpus 1 --cpu-shares 256 --memory 1536m --memory-swap 1536m --pids-limit 256 --oom-score-adj 700 \
      --log-opt max-size=10m --log-opt max-file=3 \
      -p 127.0.0.1:5060:5050 \
      -v /var/lib/csp-exam/hydro-work:/var/lib/csp-exam/hydro-work:ro \
      -v /var/lib/csp-go-judge/cache:/cache \
      csp-go-judge:gcc9.3-v1.12 \
      -http-addr 0.0.0.0:5050 -mount-conf /opt/mount.yaml -parallelism 1 -pre-fork 1 \
      -no-fallback -dir /cache -release -src-prefix /var/lib/csp-exam/hydro-work -container-cred-start 20000
fi
if ! docker container inspect csp-hydro-bridge >/dev/null 2>&1; then
    docker run -d --name csp-hydro-bridge --restart unless-stopped --network host \
      --user "$(id -u csp-exam):$(id -g csp-exam)" \
      --cpus 0.5 --cpu-shares 128 --memory 256m --memory-swap 256m --pids-limit 64 --oom-score-adj 500 \
      --cap-drop ALL --security-opt no-new-privileges --read-only \
      --tmpfs /tmp:size=32m,mode=1777 \
      --log-opt max-size=10m --log-opt max-file=3 --env-file /etc/csp-hydro.env \
      -v /var/lib/csp-exam/problems:/var/lib/csp-exam/problems:ro \
      -v /var/lib/csp-exam/hydro-work:/var/lib/csp-exam/hydro-work:rw \
      csp-hydro-bridge:4.0.6
fi
sleep 3
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
docker logs csp-go-judge --tail 8
docker logs csp-hydro-bridge --tail 12
curl --fail --max-time 5 http://127.0.0.1:8082/health
printf '\n'
