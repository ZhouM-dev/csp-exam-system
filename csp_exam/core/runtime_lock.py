"""Process lock shared by the application and offline backup tool."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def data_lock(directory):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    stream = open(path / ".service.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if not stream.tell(): stream.write(b"0"); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        stream.close()
        raise RuntimeError("数据目录正在被比赛服务或备份任务使用。请先停用本 CSP 服务。") from e
    try:
        yield
    finally:
        stream.close()  # Closing releases the OS lock, including after a crash.
