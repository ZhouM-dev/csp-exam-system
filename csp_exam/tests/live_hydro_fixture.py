"""Temporary Hydro sidecar and test data; no production problem or score is changed."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid

@contextmanager
def live_hydro_fixture(prefix='csp-live-',data_subdir=''):
    import pwd  # Linux-only fixture; offline tests also run on Windows.
    if not os.environ.get('CSP_HYDRO_TOKEN'):
        raise RuntimeError('Load the private CSP service environment before running live tests')
    work=Path(os.environ.get('CSP_EXAM_DATA_DIR','/var/lib/csp-exam'))/'hydro-work'
    account=pwd.getpwnam('csp-exam')
    with tempfile.TemporaryDirectory(prefix=prefix,dir=work) as tmp:
        base=Path(tmp);root=base/data_subdir if data_subdir else base
        root.mkdir(exist_ok=True)
        for p in (base,root,root/'hydro-work'):
            p.mkdir(exist_ok=True);os.chown(p,account.pw_uid,account.pw_gid)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        name='csp-qa-hydro-'+uuid.uuid4().hex[:10]
        old=os.environ.get('CSP_HYDRO_URL')
        original_uid,original_gid=os.geteuid(),os.getegid()
        try:
            subprocess.run(['docker','run','-d','--rm','--name',name,'--network','host',
                '--user',f'{account.pw_uid}:{account.pw_gid}','--memory','192m','--memory-swap','192m',
                '--cpus','0.25','--pids-limit','64','--cap-drop','ALL','--read-only',
                '--security-opt','no-new-privileges','--tmpfs','/tmp:size=32m,mode=1777',
                '--env','CSP_HYDRO_TOKEN','--env',f'CSP_EXAM_DATA_DIR={root}',
                '--env',f'CSP_HYDRO_PORT={port}','-v',f'{work}:{work}:rw',
                'csp-hydro-bridge:4.0.6'],check=True,stdout=subprocess.DEVNULL)
            address=f'http://127.0.0.1:{port}'
            deadline=time.monotonic()+15
            while True:
                try:
                    with urllib.request.urlopen(address+'/health',timeout=1):break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('Temporary Hydro sidecar did not start')
                    time.sleep(.1)
            os.environ['CSP_HYDRO_URL']=address
            # Exercise application writes as its real service account. Root-created
            # 0700 test-data directories otherwise hide fixtures from the sidecar.
            os.setegid(account.pw_gid)
            os.seteuid(account.pw_uid)
            try:
                yield tmp
            finally:
                os.seteuid(original_uid)
                os.setegid(original_gid)
        finally:
            subprocess.run(['docker','stop','-t','3',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if old is None:os.environ.pop('CSP_HYDRO_URL',None)
            else:os.environ['CSP_HYDRO_URL']=old
