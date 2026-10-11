"""Private, consistent and portable problem ZIPs; no contests or credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from . import database, judgelocal, localoj, problems, store
from .archive import safe_name

MANIFEST = "csp-package.json"
FORMAT = "csp-problem/1"


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def validate_package(bundle, children):
    """Validate every byte and reference before any import changes local state."""
    meta = json.loads(bundle.read(children[MANIFEST]).decode("utf-8"))
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise ValueError("不支持的 CSP 题包版本")
    if meta.get("score_mode") != "independent-cases":
        raise ValueError("题包计分方式不受支持")
    hashes = meta.get("sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(children) - {MANIFEST}:
        raise ValueError("题包文件清单不完整或包含额外文件")
    for name, digest in hashes.items():
        if safe_name(name) != name or not isinstance(digest, str):
            raise ValueError("题包文件清单无效")
        if hashlib.sha256(bundle.read(children[name])).hexdigest() != digest:
            raise ValueError(f"题包文件校验失败：{name}")
    cases, names, expected = [], set(), {"题目.md", "problem.json", "liteoj-seed.json"}
    if "标程.cpp" in children:
        expected.add("标程.cpp")
    if not isinstance(meta.get("cases"), list) or not meta["cases"]:
        raise ValueError("题包没有测试点")
    for case in meta["cases"]:
        if not isinstance(case, dict):
            raise ValueError("测试点清单无效")
        name = case.get("name")
        if not isinstance(name, str) or safe_name(name) != name or "/" in name or name in names:
            raise ValueError("测试点名称无效或重复")
        paths = [f"data/{name}.in", f"data/{name}.out"]
        if any(p not in children for p in paths):
            raise ValueError(f"测试点 {name} 缺少输入或答案")
        names.add(name)
        expected.update(paths)
        cases.append((name, children[paths[0]], children[paths[1]]))
    items = meta.get("samples")
    if not isinstance(items, list):
        raise ValueError("样例清单无效")
    sample_names = set()
    for item in items:
        if (not isinstance(item, dict) or item.get("kind") not in ("big", "sample")
                or not isinstance(item.get("label"), str) or not isinstance(item.get("files"), list)):
            raise ValueError("样例分类或标签无效")
        roles = set()
        for file in item["files"]:
            if not isinstance(file, dict):
                raise ValueError("样例文件清单无效")
            name, role = file.get("name"), file.get("role")
            if (not isinstance(name, str) or safe_name(name) != name or "/" in name
                    or name == "manifest.json" or name in sample_names
                    or role not in ("in", "out", "other") or role != "other" and role in roles):
                raise ValueError("样例文件名或角色无效")
            rel = "samples/" + name
            if (rel not in children or type(file.get("size")) is not int
                    or file["size"] != len(bundle.read(children[rel]))):
                raise ValueError(f"样例文件缺失或大小不符：{name}")
            sample_names.add(name)
            roles.add(role)
            expected.add(rel)
    if set(hashes) != expected:
        raise ValueError("题包包含未声明的数据或样例文件")
    return {"cases": cases, "samples": items}


def restore_samples(pid, items, files, children):
    """Keep kinds, labels, roles and original filenames, including empty sets."""
    root = Path(problems._samples_root(pid))
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    for item in items:
        for file in item["files"]:
            (root / file["name"]).write_bytes(files[children["samples/" + file["name"]]])
    store._save(str(root / "manifest.json"), {"pid": pid, "items": items})
    return {"items": items, "files": sum(len(i["files"]) for i in items),
            "bytes": sum(f["size"] for i in items for f in i["files"]), "dir": str(root)}


def build_zip(output, selected=None, *, max_bytes=None):
    """Snapshot under the writer locks, then compress without blocking writers.

    The running server may use this. Only the offline CLI takes the process lock.
    Staging is private and file contents are streamed rather than held in memory.
    """
    output = Path(output).resolve()
    if output.is_relative_to(Path(store.DATA_DIR).resolve()):
        raise ValueError("导出包须保存在运行数据目录之外。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="csp-problem-export-") as tmp:
        stage, hashes, total = Path(tmp), {}, 0

        def put(name, data=None, source=None, root=None):
            nonlocal total
            if safe_name(name) != name or name in hashes:
                raise ValueError("题库文件名无效或重复，不能完整导出。")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            if source is not None:
                source, root = Path(source), Path(root).resolve()
                if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
                    raise ValueError("题库文件缺失或路径异常，不能完整导出。")
            with target.open("wb") as out:
                if source is None:
                    out.write(data)
                    digest.update(data)
                    total += len(data)
                else:
                    with source.open("rb") as stream:
                        while chunk := stream.read(256 * 1024):
                            out.write(chunk)
                            digest.update(chunk)
                            total += len(chunk)
                            if max_bytes is not None and total > max_bytes:
                                raise ValueError("题包超过本部署的题目上传上限，请先调整 CSP_MAX_PS_UPLOAD_MB。")
            hashes[name] = digest.hexdigest()
            if len(hashes) > 20000 or max_bytes is not None and total > max_bytes:
                raise ValueError("题包超过本部署的题目上传上限，请先调整 CSP_MAX_PS_UPLOAD_MB。")

        with store._LOCK, database.transaction(store.DATA_DIR):
            rows = [r for r in localoj.list_problems() if not problems.is_deleted(r["pid"])]
            if selected is not None:
                rows = [r for r in rows if r["pid"] in selected]
                if set(selected) != {r["pid"] for r in rows}:
                    raise ValueError("指定题目不存在或已删除。")
            if not rows:
                raise ValueError("没有可导出的题目。")
            info = problems.load_problem_info()
            for row in rows:
                pid, rec = row["pid"], info.get(row["pid"]) or {}
                if not judgelocal.valid_pid(pid):
                    raise ValueError("题库标识无效。")
                head, text = pid + "/", localoj.problem_statement(pid)
                time_ms, memory_mb = judgelocal.limits_of(pid)
                config = {"id": pid, "title": row["title"], "name": problems.name_of_pid(pid),
                          "time_ms": time_ms, "memory_mb": memory_mb}
                put(head + "题目.md", text.encode("utf-8"))
                if rec.get("std"):
                    put(head + "标程.cpp", rec["std"].encode("utf-8"))
                data_root = Path(judgelocal.cases_dir(pid))
                case_names = sorted((p.stem for p in data_root.glob("*.in")), key=judgelocal._natural)
                if not case_names or {p.stem for p in data_root.glob("*.out")} != set(case_names):
                    raise ValueError(f"题目 {pid} 的测试数据缺失或不成对，不能导出。")
                if rec.get("cases") != len(case_names):
                    raise ValueError(f"题目 {pid} 的测试点数量与登记不一致，请先检查数据。")
                config["case_count"] = len(case_names)
                put(head + "problem.json", _json(config))
                for name in case_names:
                    for ext in ("in", "out"):
                        put(head + f"data/{name}.{ext}", source=data_root / f"{name}.{ext}", root=data_root)
                sample_root = Path(problems._samples_root(pid))
                items = store._load(str(sample_root / "manifest.json"), dict).get("items", [])
                for item in items:
                    for file in item["files"]:
                        put(head + "samples/" + file["name"], source=sample_root / file["name"], root=sample_root)
                seed = {"id": pid, "title": row["title"], "description": text,
                        "timeLimit": time_ms, "memoryLimit": memory_mb, "checkerMode": "standard",
                        "isPublic": 0, "tags": [], "difficulty": "unrated",
                        "cases": [{"inputPath": f"problems/{pid}/testdata/{name}.in",
                                   "outputPath": f"problems/{pid}/testdata/{name}.out",
                                   "score": 100 // len(case_names) + (i < 100 % len(case_names)), "sort": i + 1}
                                  for i, name in enumerate(case_names)],
                        "csp": {"submissionName": config["name"], "problemNumber": problems.code_of_pid(pid),
                                "scoreMode": "independent-cases"}}
                put(head + "liteoj-seed.json", _json(seed))
                meta = {"format": FORMAT, "source_number": problems.code_of_pid(pid),
                        "source_updated_at": rec.get("at", ""), "score_mode": "independent-cases",
                        "cases": [{"name": name} for name in case_names], "samples": items,
                        "sha256": {n[len(head):]: h for n, h in hashes.items() if n.startswith(head)}}
                put(head + MANIFEST, _json(meta))
                from .importer import Bundle
                bundle = Bundle(str(stage / pid))
                validate_package(bundle, {n: n for n in bundle.names()})
        put("library-manifest.json", _json({"format": "csp-library/2", "problems": [r["pid"] for r in rows],
                                           "sha256": dict(hashes)}))
        created = False
        try:
            with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                created = True
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                    for name in hashes:
                        archive.write(stage / name, name)
        except Exception:
            if created:
                output.unlink(missing_ok=True)
            raise
    return {"file": str(output), "problems": len(rows), "files": len(hashes), "bytes": total}
