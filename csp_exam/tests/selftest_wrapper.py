"""自测：freopen 检查 + 代码包装（按题目英文名），以及包装后的 C/C++ 能否编译运行。

接口现在是 wrap(source, base) / check_freopen(source, base)，base 是题目英文名
（既决定 <英文名>.in/.out 文件名，也是 CSP 赛制下的文件夹名）。
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core import hydro_client, importer, problems, store, wrapper  # noqa: E402
import wrapper

OK, BAD = [], []


def expect(name, got, want):
    (OK if got == want else BAD).append(name)
    print(f"  {'✓' if got == want else '✗'} {name}" + ("" if got == want else f"（期望 {want}，实际 {got}）"))


print("=== 1. freopen 检查（按英文名）===")
expect("正确写法", wrapper.check_freopen('freopen("candy.in","r",stdin);', "candy"), "")
expect("没写 freopen -> 有提示", bool(wrapper.check_freopen('int main(){return 0;}', "candy")), True)
expect("文件名写错 -> 有提示", bool(wrapper.check_freopen('freopen("Candy.in","r",stdin);', "candy")), True)
expect("换了题目英文名也要对得上", wrapper.check_freopen('freopen("meal.in", "r", stdin);', "meal"), "")
expect("写成别的题的文件名 -> 有提示",
       bool(wrapper.check_freopen('freopen("candy.in", "r", stdin);', "meal")), True)

print()
print("=== 2. 包装（按英文名）===")
code = wrapper.wrap('int main(){return 0;}', "candy")
expect("含 candy.in", "candy.in" in code, True)
expect("含 candy.out", "candy.out" in code, True)
expect("学生代码被保留在末尾", code.rstrip().endswith("int main(){return 0;}"), True)
expect("不同题生成不同文件名", "meal.in" in wrapper.wrap("int main(){}", "meal"), True)

print()
print("=== 3. 用真实编译器验证包装能编译并跑通 ===")
gxx = r"C:\msys64\ucrt64\bin\g++.exe"
gcc = r"C:\msys64\ucrt64\bin\gcc.exe"
if not os.path.isfile(gxx):
    print("  跳过（本机没有 g++）")
else:
    with tempfile.TemporaryDirectory() as d:
        src = wrapper.wrap("""#include <bits/stdc++.h>
using namespace std;
int main(){ freopen("candy.in","r",stdin); freopen("candy.out","w",stdout);
  long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }""", "candy")
        f = os.path.join(d, "t.cpp")
        open(f, "w", encoding="utf-8").write(src)
        r = subprocess.run([gxx, "-O2", "-std=c++14", "-o", "t.exe", "t.cpp"],
                           cwd=d, capture_output=True, text=True)
        expect("C++ 包装后能编译", r.returncode == 0, True)
        if r.returncode != 0:
            print("     编译错误:", (r.stderr or "")[:300])
        else:
            open(os.path.join(d, "candy.in"), "w").write("1 2\n")
            p = subprocess.run([os.path.join(d, "t.exe")], cwd=d,
                               input="1 2\n", capture_output=True, text=True)
            expect("输出被带回 stdout（3）", p.stdout.strip(), "3")
            expect("生成了 candy.out", os.path.isfile(os.path.join(d, "candy.out")), True)

        src_c = wrapper.wrap("""#include <stdio.h>
int main(void){ FILE* f=fopen("candy.in","r"); long long a,b; fscanf(f,"%lld %lld",&a,&b);
  printf("%lld\\n", a+b); return 0; }""", "candy")
        fc = os.path.join(d, "tc.c")
        open(fc, "w", encoding="utf-8").write(src_c)
        r2 = subprocess.run([gcc, "-O2", "-std=c11", "-o", "tc.exe", "tc.c"],
                            cwd=d, capture_output=True, text=True)
        expect("C 包装后能编译", r2.returncode == 0, True)
        if r2.returncode != 0:
            print("     编译错误:", (r2.stderr or "")[:300])

print()
print(f"结果：{len(OK)} 项通过，{len(BAD)} 项失败")
if BAD:
    print("失败：", BAD)
    sys.exit(1)
