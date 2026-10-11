"""Create a source-only release with per-file hashes; never include runtime data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ("csp_exam", "deployment", "docs", "tests", "run.py", "README.md", "LICENSE")
PRIVATE_DIRS = {"data", "logs", ".git", ".tools", ".preview", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {".py", ".js", ".cjs", ".yaml", ".yml", ".css", ".html", ".sh", ".md", ".json", ".service", ".example"}


def payload(path):
    data = path.read_bytes()
    return data.replace(b"\r\n", b"\n") if path.suffix in TEXT_SUFFIXES else data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "csp_exam/tools/run_selftests.py")], cwd=ROOT, check=True)
    files = []
    for name in INCLUDE:
        p = ROOT / name
        files.extend(sorted(p.rglob("*")) if p.is_dir() else [p])
    files = [p for p in files if p.is_file() and not p.is_symlink()
             and not PRIVATE_DIRS.intersection(p.relative_to(ROOT).parts)
             and p.suffix != ".pyc" and not p.name.startswith(".env")
             and p.name not in {"admin_key.txt", "gojudge_config.json"}]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(payload(p)).hexdigest() for p in files}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as tar:
        for p in files:
            data = payload(p)
            info = tar.gettarinfo(str(p), arcname=p.relative_to(ROOT).as_posix())
            info.size = len(data)
            info.mode = 0o755 if p.suffix == ".sh" else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
        data = json.dumps({"base": "origin/dev 2fb981ac22c52cf18db1128f72d34cc99c7da88a", "sha256": hashes}, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo("RELEASE-MANIFEST.json"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    print(f"Release: {args.output.resolve()} ({len(files)} files)")


if __name__ == "__main__": main()
