"""Fetch pinned, unmodified Tabler assets and retain upstream MIT notices."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

VERSION = "1.6.1"
ROOT = Path(__file__).resolve().parents[1] / "web/static/vendor/tabler" / VERSION


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def package(name, version):
    meta = json.loads(fetch(f"https://registry.npmjs.org/{name}/{version}"))
    data = fetch(meta["dist"]["tarball"])
    algorithm, digest = meta["dist"]["integrity"].split("-", 1)
    if hashlib.new(algorithm, data).digest() != base64.b64decode(digest):
        raise ValueError("npm integrity mismatch")
    return meta, tarfile.open(fileobj=io.BytesIO(data))


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    meta, archive = package("@tabler/core", VERSION)
    files = {}
    with archive:
        for filename, member in (("tabler.min.css", "package/dist/css/tabler.min.css"),
                                 ("tabler.min.js", "package/dist/js/tabler.min.js")):
            files[filename] = archive.extractfile(member).read()
    # The npm archive omits Tabler's own LICENSE; the bundled asset header links here.
    files["LICENSE-Tabler.txt"] = fetch("https://raw.githubusercontent.com/tabler/tabler/master/LICENSE")
    for name, version, label in (("bootstrap", "5.3.8", "Bootstrap"), ("@popperjs/core", "2.11.8", "Popper")):
        _, archive = package(name, version)
        with archive:
            member = next(m for m in archive.getmembers() if m.isfile()
                          and m.name.rsplit("/", 1)[-1].lower() in ("license", "license.md", "license.txt"))
            files[f"LICENSE-{label}.txt"] = archive.extractfile(member).read()
    for filename, data in files.items():
        (ROOT / filename).write_bytes(data)
    manifest = {"name": "@tabler/core", "version": VERSION, "license": "MIT",
                "tarball": meta["dist"]["tarball"], "integrity": meta["dist"]["integrity"],
                "upstream": "https://github.com/tabler/tabler", "assets_modified": False,
                "layout_source": "https://docs.tabler.io/ui/layout/page-layouts",
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (ROOT / "vendor-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Vendored Tabler {VERSION}: {len(files)} files; npm integrity verified.")


if __name__ == "__main__":
    main()
