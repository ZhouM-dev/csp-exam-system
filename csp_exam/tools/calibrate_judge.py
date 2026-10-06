#!/usr/bin/env python3
"""用固定受控基准估算本机/参考机 CPU 时间比，绝不捏造官方基准。

参考机：python3 -m csp_exam.tools.calibrate_judge --native --label '参考机配置' --output reference.json
评测机：python3 -m csp_exam.tools.calibrate_judge --reference reference.json --output local.json
检查离散程度后加 --apply 才写入倍率。--native 只编译这里固定的基准，不能传入选手源码。
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from csp_exam.config import JUDGE_SLOTS
from csp_exam.core import gojudge, judge_profile

FLAGS = ["-O2", "-std=c++14", "-static"]
SOURCE = r'''
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
static uint64_t rng(uint64_t &x) { x^=x<<13; x^=x>>7; x^=x<<17; return x; }
int main(int argc,char **argv) {
  if(argc!=2)return 1;
  int kind=atoi(argv[1]); uint64_t x=123456789, sum=0;
  if(kind==0) {
    for(unsigned i=0;i<200000000;i++){rng(x); sum+=x;}
  } else if(kind==1) {
    std::vector<uint32_t> a(2000000);
    for(auto &v:a)v=rng(x);
    std::sort(a.begin(),a.end());
    for(auto v:a)sum=sum*33+v;
  } else if(kind==2) {
    std::vector<uint32_t> a(1<<24);
    for(unsigned i=0;i<a.size();i++)a[i]=rng(x);
    for(unsigned i=0;i<32000000;i++){sum+=a[rng(x)&((1<<24)-1)];}
  } else return 1;
  printf("%llu\n",(unsigned long long)sum);return 0;
}
'''
WORKLOADS = ["integer", "sort", "memory"]
SOURCE_HASH = hashlib.sha256(SOURCE.encode()).hexdigest()


def machine():
    model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        model = next((ln.split(":", 1)[1].strip() for ln in cpuinfo.read_text().splitlines()
                      if ln.startswith("model name")), "unknown")
    return {"cpu": model, "kernel": platform.release(), "cpus": os.cpu_count()}


def benchmark(repeats=5, *, native=False, label=""):
    out = {"schema": 1, "source_sha256": SOURCE_HASH, "flags": FLAGS,
           "label": label, "machine": machine(), "method": "native" if native else "go-judge",
           "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "benchmarks": {}}
    if native:
        import resource
        version = subprocess.check_output(["g++", "--version"], text=True).splitlines()[0]
        if not version.endswith(" 9.3.0"):
            raise ValueError("参考机也必须使用 G++ 9.3.0，实际 " + version)
        out["compiler"] = version
        with tempfile.TemporaryDirectory(prefix="csp-benchmark-") as temp:
            source, binary = Path(temp) / "benchmark.cpp", Path(temp) / "benchmark"
            source.write_text(SOURCE)
            subprocess.run(["g++", *FLAGS, str(source), "-o", str(binary)], check=True, timeout=60)
            def one(kind):
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                answer = subprocess.check_output([str(binary), str(kind)], timeout=60)
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                return (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime) * 1000, answer
            _samples(out, one, repeats)
    else:
        with JUDGE_SLOTS:
            out["compiler"] = gojudge.ensure_ready()["compiler"]
            src_id, bin_id = gojudge.prepare(SOURCE.encode()), ""
            try:
                r = gojudge.run(["/usr/bin/g++", *FLAGS, "benchmark.cpp", "-o", "benchmark"],
                                copy_in={"benchmark.cpp": {"fileId": src_id}}, cpu_ms=30000,
                                memory_mb=2048, cached=["benchmark"])
                bin_id = (r.get("fileIds") or {}).get("benchmark", "")
                if r.get("status") != "Accepted" or not bin_id:
                    raise ValueError("基准编译失败：" + str(r.get("files")))
                def one(kind):
                    r = gojudge.run(["./benchmark", str(kind)], copy_in={"benchmark": {"fileId": bin_id}},
                                    cpu_ms=10000, memory_mb=512, stack_mb=512, proc_limit=1)
                    if r.get("status") != "Accepted":
                        raise ValueError("基准运行失败：" + str(r.get("status")))
                    return int(r["time"]) / 1e6, r["files"]["stdout"]
                _samples(out, one, repeats)
            finally:
                gojudge.drop(src_id)
                gojudge.drop(bin_id)
    return out


def _samples(out, one, repeats):
    for kind, name in enumerate(WORKLOADS):
        samples, checksums = [], []
        one(kind)  # 热身，不计入样本。
        for _ in range(repeats):
            ms, answer = one(kind)
            samples.append(round(ms, 3))
            checksums.append(hashlib.sha256(answer).hexdigest())
        if len(set(checksums)) != 1:
            raise ValueError("基准输出不稳定")
        median = statistics.median(samples)
        out["benchmarks"][name] = {"samples_ms": samples, "median_ms": median,
                                   "checksum": checksums[0],
                                   "spread": (max(samples) - min(samples)) / median}


def compare(local, reference):
    if (local["source_sha256"] != reference.get("source_sha256") or
            local["flags"] != reference.get("flags") or
            local["compiler"] != reference.get("compiler")):
        raise ValueError("参考与本机的基准源码、编译器版本、参数必须一致")
    ratios, warnings = {}, []
    for name in WORKLOADS:
        a, b = local["benchmarks"][name], reference["benchmarks"][name]
        if a["checksum"] != b["checksum"] or min(a["median_ms"], b["median_ms"]) < 100:
            raise ValueError("基准输出不同或样本太短，不能标定")
        ratios[name] = a["median_ms"] / b["median_ms"]
        if max(a["spread"], b["spread"]) > .15:
            warnings.append(f"{name} 的重复样本波动超过 15%")
    scale = statistics.median(ratios.values())
    if (max(ratios.values()) - min(ratios.values())) / scale > .25:
        warnings.append("不同负载的性能比例差异超过 25%，不宜应用统一倍率")
    return {"time_scale": round(scale, 4), "ratios": ratios, "warnings": warnings,
            "reference_label": reference.get("label", ""), "reference_machine": reference.get("machine", {}),
            "reference_sha256": hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--native", action="store_true")
    p.add_argument("--label", default="")
    p.add_argument("--output", required=True)
    p.add_argument("--reference")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--repeats", type=int, default=5)
    args = p.parse_args()
    if not 3 <= args.repeats <= 20 or (args.apply and not args.reference):
        p.error("重复次数须为 3～20；应用倍率必须提供参考机数据")
    local = benchmark(args.repeats, native=args.native, label=args.label)
    if args.reference:
        local["comparison"] = compare(local, json.loads(Path(args.reference).read_text()))
    Path(args.output).write_text(json.dumps(local, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(local, ensure_ascii=False, indent=2))
    if args.apply:
        comparison = local["comparison"]
        if comparison["warnings"]:
            raise ValueError("样本存在警告，未应用倍率；请稳定负载或使用匹配的参考硬件后重测")
        judge_profile.save(comparison["time_scale"], calibrated=True,
                           evidence={"method": "benchmark", "local": local})


if __name__ == "__main__":
    main()
