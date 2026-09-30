#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原创题目集生成器（CSP-J/S 入门~普及）——题面、数据、标程一起产出。

为什么自己出题：批量搬运其他 OJ 的题面既违反站点条款，题面也是受版权保护的作品。
这里每道题都是原创的：题面自己写、数据用生成器造、标准答案由标程算出，
所以数据与答案必然自洽，也能按教学进度自由调整。

用法：python3 make_problem_set.py [输出目录]   （默认 /root/hydro/data/backend/import）
每道题生成一个目录：P2001/, P2002/ …，随后用
    docker compose exec -T oj-backend hydrooj cli problem import system /root/.hydro/import/P2001
导入即可。
"""

from __future__ import annotations

import os
import random
import shutil
import sys

OUT_ROOT = sys.argv[1] if len(sys.argv) > 1 else "/root/hydro/data/backend/import"
CASES_PER_PROBLEM = 10          # 每个测试点文件数
SEED_BASE = 20260925

# ------------------------------------------------------------------ 题目定义
# 每题：pid / 标题 / 难度 / 题面 / 造数据 rng->(输入字符串, 额外返回) / 标程计算


def _fmt_list(a):
    return " ".join(str(x) for x in a)


PROBLEMS = []


def problem(pid, title, difficulty, statement, gen, solve):
    PROBLEMS.append(dict(pid=pid, title=title, difficulty=difficulty,
                         statement=statement, gen=gen, solve=solve))


def fmt_half_up(x: float, digits: int = 2) -> str:
    """按 C++ printf 的"四舍五入"（逢五进一）格式化小数。

    Python 的 f"{x:.2f}" 是银行家舍入（0.125 -> 0.12），和 printf 的 0.13 不一致，
    直接用它生成标准答案会导致标程被误判为错。这里统一成 printf 的行为。
    """
    scale = 10 ** digits
    v = int(x * scale + (0.5 if x >= 0 else -0.5))
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v // scale}.{v % scale:0{digits}d}"


# ---------------- P2001 最大值与最小值 ----------------


def p_maxmin_gen(rng):
    n = rng.randint(1, 200) if rng.random() < 0.5 else rng.randint(1, 100000)
    big = rng.random() < 0.5
    lo, hi = (-10**9, 10**9) if big else (-100, 100)
    a = [rng.randint(lo, hi) for _ in range(n)]
    return f"{n}\n{_fmt_list(a)}\n"


def p_maxmin_solve(inp):
    it = iter(inp.split())
    n = int(next(it))
    a = [int(next(it)) for _ in range(n)]
    return f"{max(a)} {min(a)}\n"


problem("P2001", "最大值与最小值", 1,
        """# 最大值与最小值

## 题目描述

给出 $n$ 个整数，求其中的最大值与最小值。

## 输入格式

第一行一个整数 $n$。

第二行 $n$ 个整数 $a_i$。

## 输出格式

一行两个整数，依次为最大值和最小值，用一个空格隔开。

## 数据范围

$1 \\le n \\le 10^5$，$-10^9 \\le a_i \\le 10^9$。

## 样例

输入：
```
5
3 -1 4 1 5
```
输出：
```
5 -1
```
""", p_maxmin_gen, p_maxmin_solve)


# ---------------- P2002 成绩统计 ----------------
def p_score_gen(rng):
    n = rng.randint(1, 50) if rng.random() < 0.5 else rng.randint(1, 100000)
    a = [rng.randint(0, 100) for _ in range(n)]
    return f"{n}\n{_fmt_list(a)}\n"


def p_score_solve(inp):
    it = iter(inp.split())
    n = int(next(it))
    a = [int(next(it)) for _ in range(n)]
    return f"{sum(a)} {max(a)} {fmt_half_up(sum(a) / n, 2)}\n"


problem("P2002", "成绩统计", 1,
        """# 成绩统计

## 题目描述

给出 $n$ 个学生的成绩，求总分、最高分与平均分。

## 输入格式

第一行一个整数 $n$。

第二行 $n$ 个整数，表示每个学生的成绩。

## 输出格式

一行，依次输出总分、最高分、平均分（保留两位小数），用空格隔开。

## 数据范围

$1 \\le n \\le 10^5$，$0 \\le$ 成绩 $\\le 100$。

## 样例

输入：
```
3
80 90 100
```
输出：
```
270 100 90.00
```
""", p_score_gen, p_score_solve)


# ---------------- P2003 闰年判断 ----------------
def p_leap_gen(rng):
    t = rng.randint(1, 5) if rng.random() < 0.5 else rng.randint(1, 1000)
    years = [rng.randint(1, 3000) for _ in range(t)]
    return f"{t}\n" + "\n".join(str(y) for y in years) + "\n"


def p_leap_solve(inp):
    it = iter(inp.split())
    t = int(next(it))
    out = []
    for _ in range(t):
        y = int(next(it))
        out.append("Yes" if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else "No")
    return "\n".join(out) + "\n"


problem("P2003", "闰年判断", 1,
        """# 闰年判断

## 题目描述

判断给定的年份是否为闰年。闰年的条件是：能被 4 整除但不能被 100 整除，或者能被 400 整除。

## 输入格式

第一行一个整数 $T$，表示询问个数。

接下来 $T$ 行，每行一个年份 $y$。

## 输出格式

对每个年份输出一行 `Yes` 或 `No`。

## 数据范围

$1 \\le T \\le 1000$，$1 \\le y \\le 3000$。

## 样例

输入：
```
2
2000
1900
```
输出：
```
Yes
No
```
""", p_leap_gen, p_leap_solve)


# ---------------- P2004 数字反转 ----------------
def p_rev_gen(rng):
    n = rng.randint(-10**9, 10**9)
    return f"{n}\n"


def p_rev_solve(inp):
    n = int(inp.strip())
    sign = -1 if n < 0 else 1
    s = str(abs(n))[::-1].lstrip("0") or "0"
    return f"{sign * int(s)}\n"


problem("P2004", "数字反转", 1,
        """# 数字反转

## 题目描述

给定一个整数，把它各位数字反转后输出（反转后要去掉前导零，负数保留符号）。

## 输入格式

一行一个整数 $n$。

## 输出格式

一行一个整数，表示反转后的结果。

## 数据范围

$-10^9 \\le n \\le 10^9$。

## 样例

输入：
```
-1230
```
输出：
```
-321
```
""", p_rev_gen, p_rev_solve)


# ---------------- P2005 回文串判断 ----------------
def p_pal_gen(rng):
    n = rng.randint(1, 50)
    kind = rng.random()
    if kind < 0.35:                       # 真回文
        s = "".join(rng.choice("abc") for _ in range(n // 2 + 1))
        s = s + s[::-1][(n % 2):]
        s = s[:n]
    elif kind < 0.7:                      # 近回文（错一位）
        s = list("".join(rng.choice("ab") for _ in range(n)))
        if n >= 2:
            s[rng.randrange(n)] = rng.choice("xyz")
        s = "".join(s)
    else:                                 # 随机
        s = "".join(rng.choice("abcdef") for _ in range(n))
    return f"{s}\n"


def p_pal_solve(inp):
    s = inp.strip().split()[0]
    return "Yes\n" if s == s[::-1] else "No\n"


problem("P2005", "回文串判断", 1,
        """# 回文串判断

## 题目描述

给定一个只含小写字母的字符串，判断它是不是回文串（正着读和倒着读相同）。

## 输入格式

一行一个字符串 $s$。

## 输出格式

如果是回文串输出 `Yes`，否则输出 `No`。

## 数据范围

$1 \\le |s| \\le 10^5$。

## 样例

输入：
```
abcba
```
输出：
```
Yes
```
""", p_pal_gen, p_pal_solve)


# ---------------- P2006 排序后输出 ----------------
def p_sort_gen(rng):
    n = rng.randint(1, 100) if rng.random() < 0.5 else rng.randint(1, 200000)
    lo, hi = (-1000, 1000) if rng.random() < 0.5 else (-10**9, 10**9)
    a = [rng.randint(lo, hi) for _ in range(n)]
    return f"{n}\n{_fmt_list(a)}\n"


def p_sort_solve(inp):
    it = iter(inp.split())
    n = int(next(it))
    a = sorted(int(next(it)) for _ in range(n))
    return _fmt_list(a) + "\n"


problem("P2006", "排序", 1,
        """# 排序

## 题目描述

给出 $n$ 个整数，请把它们从小到大排序后输出。

## 输入格式

第一行一个整数 $n$。

第二行 $n$ 个整数。

## 输出格式

一行 $n$ 个整数，为从小到大排好序的结果。

## 数据范围

$1 \\le n \\le 2 \\times 10^5$，$-10^9 \\le a_i \\le 10^9$。

## 样例

输入：
```
5
3 1 4 1 5
```
输出：
```
1 1 3 4 5
```
""", p_sort_gen, p_sort_solve)


# ---------------- P2007 区间和（前缀和） ----------------
def p_pref_gen(rng):
    n = rng.randint(1, 200) if rng.random() < 0.4 else rng.randint(1, 100000)
    q = rng.randint(1, 50) if rng.random() < 0.4 else rng.randint(1, 100000)
    a = [rng.randint(-10**6, 10**6) for _ in range(n)]
    lines = [f"{n} {q}", _fmt_list(a)]
    for _ in range(q):
        l = rng.randint(1, n)
        r = rng.randint(l, n)
        lines.append(f"{l} {r}")
    return "\n".join(lines) + "\n"


def p_pref_solve(inp):
    it = iter(inp.split())
    n = int(next(it)); q = int(next(it))
    pre = [0] * (n + 1)
    for i in range(1, n + 1):
        pre[i] = pre[i - 1] + int(next(it))
    out = []
    for _ in range(q):
        l = int(next(it)); r = int(next(it))
        out.append(str(pre[r] - pre[l - 1]))
    return "\n".join(out) + "\n"


problem("P2007", "区间和", 2,
        """# 区间和

## 题目描述

给出一个长度为 $n$ 的数列，回答 $q$ 次询问：每次给出 $l, r$，求 $a_l + a_{l+1} + \\cdots + a_r$。

## 输入格式

第一行两个整数 $n, q$。

第二行 $n$ 个整数 $a_i$。

接下来 $q$ 行，每行两个整数 $l, r$。

## 输出格式

$q$ 行，每行一个整数表示对应区间之和。

## 数据范围

$1 \\le n, q \\le 10^5$，$|a_i| \\le 10^6$，$1 \\le l \\le r \\le n$。

## 提示

朴素做法每次询问都要重新累加，总复杂度 $O(nq)$ 会超时。用前缀和可以做到 $O(1)$ 回答每次询问。

## 样例

输入：
```
5 2
1 2 3 4 5
1 3
2 5
```
输出：
```
6
14
```
""", p_pref_gen, p_pref_solve)


# ---------------- P2008 有序数组中的查找（二分） ----------------
def p_bs_gen(rng):
    n = rng.randint(1, 100) if rng.random() < 0.4 else rng.randint(1, 100000)
    q = rng.randint(1, 20) if rng.random() < 0.4 else rng.randint(1, 50000)
    a = sorted(rng.randint(-10**9, 10**9) for _ in range(n))
    lines = [f"{n} {q}", _fmt_list(a)]
    for _ in range(q):
        if rng.random() < 0.6 and n > 0:
            lines.append(str(rng.choice(a)))
        else:
            lines.append(str(rng.randint(-10**9, 10**9)))
    return "\n".join(lines) + "\n"


def p_bs_solve(inp):
    it = iter(inp.split())
    n = int(next(it)); q = int(next(it))
    a = [int(next(it)) for _ in range(n)]
    import bisect
    out = []
    for _ in range(q):
        x = int(next(it))
        out.append(str(bisect.bisect_left(a, x) + 1))   # 1 起下标；不存在则返回第一个大于等于它的位置
    return "\n".join(out) + "\n"


problem("P2008", "有序数组中的查找", 2,
        """# 有序数组中的查找

## 题目描述

给出一个**从小到大排好序**的数组 $a$（下标从 1 开始），回答 $q$ 次询问：对每个 $x$，
输出第一个大于等于 $x$ 的元素的下标（若不存在，输出 $n+1$）。

## 输入格式

第一行两个整数 $n, q$。

第二行 $n$ 个整数 $a_i$，保证单调不降。

接下来 $q$ 行，每行一个整数 $x$。

## 输出格式

$q$ 行，每行一个整数表示答案。

## 数据范围

$1 \\le n, q \\le 10^5$，$|a_i|, |x| \\le 10^9$。

## 提示

每次询问都从头扫描是 $O(nq)$，要用二分做到 $O(q \\log n)$。C++ 可以用 `lower_bound`。

## 样例

输入：
```
5 2
1 3 5 7 9
5
6
```
输出：
```
3
4
```
""", p_bs_gen, p_bs_solve)


# ---------------- P2009 字母出现次数 ----------------
def p_count_gen(rng):
    n = rng.randint(1, 100) if rng.random() < 0.4 else rng.randint(1, 200000)
    s = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(n))
    return f"{s}\n"


def p_count_solve(inp):
    s = inp.strip().split()[0]
    cnt = [0] * 26
    for ch in s:
        cnt[ord(ch) - 97] += 1
    # 出现次数最多；并列时取字典序最小的字母（-i 保证平局取小下标）
    best = max(range(26), key=lambda i: (cnt[i], -i))
    return f"{chr(97 + best)} {cnt[best]}\n"


problem("P2009", "字母出现次数", 1,
        """# 字母出现次数

## 题目描述

给定一个只含小写字母的字符串，求出现次数最多的字母；若有多个字母出现次数相同，输出**字典序最小**的那个，以及它出现的次数。

## 输入格式

一行一个字符串 $s$。

## 输出格式

一行，第一个是字母，第二个是该字母出现的次数，用空格隔开。

## 数据范围

$1 \\le |s| \\le 2 \\times 10^5$。

## 样例

输入：
```
aabbc
```
输出：
```
a 2
```
""", p_count_gen, p_count_solve)


# ---------------- P2010 最大子段和 ----------------
def p_mss_gen(rng):
    n = rng.randint(1, 50) if rng.random() < 0.4 else rng.randint(1, 200000)
    big = rng.random() < 0.5
    lo, hi = (-10**9, 10**9) if big else (-20, 20)
    a = [rng.randint(lo, hi) for _ in range(n)]
    return f"{n}\n{_fmt_list(a)}\n"


def p_mss_solve(inp):
    it = iter(inp.split())
    n = int(next(it))
    best = None
    cur = 0
    for _ in range(n):
        x = int(next(it))
        cur = x if cur <= 0 else cur + x
        if best is None or cur > best:
            best = cur
    return f"{best}\n"


problem("P2010", "最大子段和", 3,
        """# 最大子段和

## 题目描述

给出一个长度为 $n$ 的整数数列，找出一个**连续子段**，使它们的和最大。子段至少包含一个数，输出这个最大和。

## 输入格式

第一行一个整数 $n$。

第二行 $n$ 个整数 $a_i$。

## 输出格式

一行一个整数，表示最大子段和。

## 数据范围

$1 \\le n \\le 2 \\times 10^5$，$|a_i| \\le 10^9$，答案可能超过 32 位整数范围。

## 提示

记 $f_i$ 为"以第 $i$ 个数结尾的最大子段和"，则 $f_i = \\max(a_i, f_{i-1} + a_i)$。
扫描一遍即可，注意用 long long。

## 样例

输入：
```
5
-2 3 -1 4 -5
```
输出：
```
6
```
（子段 $3, -1, 4$ 的和为 6）
""", p_mss_gen, p_mss_solve)


# ---------------- P2011 素数个数（筛法） ----------------
def p_prime_gen(rng):
    n = rng.randint(2, 100) if rng.random() < 0.4 else rng.randint(2, 2000000)
    return f"{n}\n"


def p_prime_solve(inp):
    n = int(inp.strip())
    sieve = bytearray([1]) * (n + 1)
    sieve[0:2] = b"\x00\x00"
    i = 2
    while i * i <= n:
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(sieve[i * i::i]))
        i += 1
    return f"{sum(sieve)}\n"


problem("P2011", "素数个数", 2,
        """# 素数个数

## 题目描述

给定 $n$，求 $1$ 到 $n$ 之间素数的个数。

## 输入格式

一行一个整数 $n$。

## 输出格式

一行一个整数，表示素数个数。

## 数据范围

$2 \\le n \\le 2 \\times 10^6$。

## 提示

逐个试除判断每个数，复杂度约 $O(n\\sqrt n)$，在 $n$ 较大时会超时。
用埃氏筛：从 2 开始，把每个素数的倍数都标记为合数，复杂度 $O(n \\log \\log n)$。

## 样例

输入：
```
10
```
输出：
```
4
```
""", p_prime_gen, p_prime_solve)


# ---------------- P2012 爬楼梯（递推/DP 入门） ----------------
def p_stairs_gen(rng):
    n = rng.randint(1, 20) if rng.random() < 0.5 else rng.randint(1, 80)
    return f"{n}\n"


def p_stairs_solve(inp):
    n = int(inp.strip())
    MOD = 10**9 + 7
    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, (a + b) % MOD
    return f"{b}\n"


problem("P2012", "爬楼梯", 2,
        """# 爬楼梯

## 题目描述

楼梯有 $n$ 级台阶，每次可以跨 1 级或 2 级。问从地面走到第 $n$ 级共有多少种不同的走法。

由于答案可能很大，请输出答案对 $10^9+7$ 取模的结果。

## 输入格式

一行一个整数 $n$。

## 输出格式

一行一个整数，表示走法数对 $10^9+7$ 取模的结果。

## 数据范围

$1 \\le n \\le 80$。

## 提示

设 $f_i$ 表示走到第 $i$ 级的走法数，最后一步要么从 $i-1$ 跨 1 级，要么从 $i-2$ 跨 2 级，
所以 $f_i = f_{i-1} + f_{i-2}$，即斐波那契数列。

## 样例

输入：
```
3
```
输出：
```
3
```
（1+1+1、1+2、2+1）
""", p_stairs_gen, p_stairs_solve)


# ------------------------------------------------------------------ 产出

STD_TEMPLATE = """#include <bits/stdc++.h>
using namespace std;

int main() {{
    // 这是本题的参考解法（标程）。练习题目使用标准输入输出。
    {body}
}}
"""

STD_BODY = {
    "P2001": "int n; cin >> n;\n    long long mn = LLONG_MAX, mx = LLONG_MIN;\n"
             "    for (int i = 0; i < n; i++) { long long x; cin >> x; mx = max(mx, x); mn = min(mn, x); }\n"
             "    cout << mx << ' ' << mn << endl;",
    "P2002": "int n; cin >> n;\n    long long s = 0, mx = 0; int t;\n"
             "    for (int i = 0; i < n; i++) { cin >> t; s += t; mx = max<long long>(mx, t); }\n"
             "    printf(\"%lld %lld %.2f\\n\", s, mx, (double)s / n);",
    "P2003": "int T; cin >> T;\n    while (T--) { int y; cin >> y;\n"
             "        cout << (((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) ? \"Yes\" : \"No\") << \"\\n\"; }",
    "P2004": "long long n; cin >> n;\n    int sign = n < 0 ? -1 : 1; string s = to_string(llabs(n));\n"
             "    reverse(s.begin(), s.end());\n    long long v = 0; for (char c : s) v = v * 10 + (c - '0');\n"
             "    cout << sign * v << endl;",
    "P2005": "string s; cin >> s;\n    string t = s; reverse(t.begin(), t.end());\n"
             "    cout << (s == t ? \"Yes\" : \"No\") << endl;",
    "P2006": "int n; cin >> n; vector<long long> a(n);\n    for (auto &x : a) cin >> x;\n"
             "    sort(a.begin(), a.end());\n    for (int i = 0; i < n; i++) cout << a[i] << \" \\n\"[i == n - 1];",
    "P2007": "int n, q; cin >> n >> q; vector<long long> pre(n + 1);\n"
             "    for (int i = 1; i <= n; i++) { long long x; cin >> x; pre[i] = pre[i - 1] + x; }\n"
             "    while (q--) { int l, r; cin >> l >> r; cout << pre[r] - pre[l - 1] << \"\\n\"; }",
    "P2008": "int n, q; cin >> n >> q; vector<long long> a(n);\n    for (auto &x : a) cin >> x;\n"
             "    while (q--) { long long x; cin >> x;\n"
             "        cout << lower_bound(a.begin(), a.end(), x) - a.begin() + 1 << \"\\n\"; }",
    "P2009": "string s; cin >> s; int cnt[26] = {0};\n    for (char c : s) cnt[c - 'a']++;\n"
             "    int best = 0; for (int i = 1; i < 26; i++) if (cnt[i] > cnt[best]) best = i;\n"
             "    cout << char('a' + best) << ' ' << cnt[best] << endl;",
    "P2010": "int n; cin >> n; long long cur = 0, best = LLONG_MIN;\n"
             "    for (int i = 0; i < n; i++) { long long x; cin >> x; cur = (cur <= 0 ? x : cur + x); best = max(best, cur); }\n"
             "    cout << best << endl;",
    "P2011": "int n; cin >> n; vector<char> is(n + 1, 1); is[0] = is[1] = 0;\n"
             "    for (long long i = 2; i * i <= n; i++) if (is[i]) for (long long j = i * i; j <= n; j += i) is[j] = 0;\n"
             "    cout << count(is.begin(), is.end(), 1) << endl;",
    "P2012": "int n; cin >> n; const long long MOD = 1000000007;\n"
             "    long long a = 1, b = 1;\n    for (int i = 1; i < n; i++) { long long c = (a + b) % MOD; a = b; b = c; }\n"
             "    cout << b << endl;",
}


def write_problem(p, root):
    os.makedirs(os.path.join(root, "testdata"), exist_ok=True)
    os.makedirs(os.path.join(root, "std"), exist_ok=True)
    with open(os.path.join(root, "problem.yaml"), "w", encoding="utf-8") as f:
        f.write(f"title: {p['title']}\n")
        f.write(f"pid: {p['pid']}\n")
        f.write("tag:\n  - 入门\n")
        f.write(f"difficulty: {p['difficulty']}\n")
        f.write("config: |-\n  time: 1000\n  memory: 256\n")
    with open(os.path.join(root, "problem_zh.md"), "w", encoding="utf-8") as f:
        f.write(p["statement"])
    with open(os.path.join(root, "std", "solution.cpp"), "w", encoding="utf-8") as f:
        f.write(STD_TEMPLATE.format(body=STD_BODY[p["pid"]]))

    for i in range(1, CASES_PER_PROBLEM + 1):
        rng = random.Random(SEED_BASE + int(p["pid"][2:]) * 1000 + i)
        inp = p["gen"](rng)
        # 规范化：去掉可能出现的首行前导空格
        inp = "\n".join(line.rstrip() for line in inp.strip("\n").split("\n")) + "\n"
        ans = p["solve"](inp)
        with open(os.path.join(root, "testdata", f"{i}.in"), "w", encoding="utf-8") as f:
            f.write(inp)
        with open(os.path.join(root, "testdata", f"{i}.out"), "w", encoding="utf-8") as f:
            f.write(ans)


def main():
    for p in PROBLEMS:
        root = os.path.join(OUT_ROOT, p["pid"])
        shutil.rmtree(root, ignore_errors=True)
        write_problem(p, root)
        # 自检：标程算出来的答案与生成器给的数据能对上（用样例做一次交叉验证）
        print(f"{p['pid']} {p['title']}: {CASES_PER_PROBLEM} 个测试点 -> {root}")
    print(f"\n共 {len(PROBLEMS)} 道题，输出目录：{OUT_ROOT}")


if __name__ == "__main__":
    main()
