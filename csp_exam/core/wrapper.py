"""提交文件匹配、CSP 源码限制与文件输入输出提示。评测直接编译原始源码。"""

SUPPORTED_EXTS = (".cpp", ".cc", ".cxx", ".c++", ".c")

#: 学生可能交上来的源码后缀（挑代码文件时用）
SOURCE_EXTS = (".cpp", ".cc", ".cxx", ".c++", ".c", ".pas")

#: 编译产物/数据文件（在读目录树时标出来，判分一律忽略）
JUNK_EXTS = (".exe", ".o", ".obj", ".out", ".in", ".ans", ".ilk", ".pdb", ".d",
             ".class", ".pyc")

#: 个人信息文件的后缀。广东考区要求考号目录下有一个以**本人姓名**命名的 txt。
PERSON_FILE_EXT = ".txt"

#: 严格模式：比较时**区分大小写**（真实考场 NOI Linux 的口径）。**默认打开**——
#: 系统只有一种口径（考场口径），调用方一般不用传 `strict=`，要临时放宽就传 `strict=False`。
STRICT_CASE = True


def strict_case() -> bool:
    """当前是否处于严格模式（区分大小写）。默认 True。"""
    return bool(STRICT_CASE)


def set_strict_case(flag: bool) -> None:
    """进程级地打开/关闭严格模式；只影响一次调用时，直接传 `strict=` 更好（测试会用）。"""
    global STRICT_CASE
    STRICT_CASE = bool(flag)


def _rel(f) -> str:
    """把上传的文件名归一化：反斜杠转正斜杠、去掉开头的 `/`。"""
    return str(f).replace("\\", "/").lstrip("/")


def _want_name(student_name) -> str:
    """把学生姓名归一化：去空白；顺手容忍调用方传成「学生01.txt」。"""
    want = str(student_name or "").strip()
    if want.lower().endswith(PERSON_FILE_EXT):
        want = want[:-len(PERSON_FILE_EXT)].strip()
    return want


def find_person_file(files: list, student_name: str, *,
                     strict: bool | None = None) -> str:
    """在提交的文件里找**个人信息文件**（广东考区强制：文件名＝本人姓名，如 `学生56.txt`）。

    返回命中的相对路径；没交返回空串。**这个文件不参与判分**，只用来提示"交没交"。

    宽容之处：后缀大小写都算（`.txt` / `.TXT`）；姓名默认忽略大小写，
    `strict=True` 时才要求姓名完全一致（严格模式贴近真实考场）。
    文件放在哪一层都能认（学生可能塞进子文件夹），但越靠外层越优先。

    `student_name` 为空（不知道学生姓名）时返回空串——调用方据此跳过检查。
    """
    import os.path as _p

    strict = strict_case() if strict is None else bool(strict)
    want = _want_name(student_name)
    if not want:
        return ""
    exact: list[str] = []
    loose: list[str] = []
    for f in files or []:
        name = _rel(f)
        if not name:
            continue
        stem, ext = _p.splitext(_p.basename(name))
        if ext.lower() != PERSON_FILE_EXT:
            continue
        if stem == want:
            exact.append(name)
        elif stem.lower() == want.lower():
            loose.append(name)
    hits = exact or ([] if strict else loose)
    if not hits:
        return ""
    return sorted(hits, key=lambda p: (p.count("/"), p))[0]     # 越靠外层越像"考号目录下那个"


def person_file_hint(files: list, student_name: str, *,
                     strict: bool | None = None) -> str:
    """个人信息文件的检查结论：交了返回空串，没交返回一句提示（给页面显示）。

    `student_name` 为空时返回空串（无从判断）。这条提示落在 pick_sources() 的 missing 里，
    写进成绩记录后，页面就能提示"没交个人信息文件"。
    """
    strict = strict_case() if strict is None else bool(strict)
    want = _want_name(student_name)
    if not want:
        return ""
    if find_person_file(files, want, strict=strict):
        return ""
    why = ""
    if strict:
        near = find_person_file(files, want, strict=False)
        if near:
            why = f"（找到了 {near}，但文件名的大小写与姓名不完全一致）"
    return (f"缺个人信息文件：考号目录下应交一个「{want}.txt」，文件名＝本人姓名"
            f"（广东考区强制要求，不参与判分）{why}")


def pick_sources(files: list[str], problems: list, *,
                 student_name: str = "", strict: bool | None = None,
                 strict_layout: bool = False, kaohao: str = "",
                 ) -> tuple[dict[int, str], list[str]]:
    """从学生上传的文件里找出「每题用哪个代码文件」。

    以前这里配着一套严格的目录结构校验（不合格就把整份提交退回）。现在**不再退回**：
    尽量宽松地把文件对上题目，找不到就当作"这道题没交"（自然得 0 分）。
    匹配顺序（默认忽略大小写，忽略最外层那个"考号文件夹"）：

      1. <编号>/<编号>.cpp          —— 标准 CSP 目录结构
      2. <编号>/<其它源码名>        —— 文件夹对了、且文件名不属于别的题
      3. <编号>.cpp                 —— 只交了文件没建文件夹（文件名比文件夹更可信）
      4. 以 <编号> 开头的唯一源码名
      5. 只有一道题、也只交了一个文件时，就用它（兜底，别删）

    第 2 条以前是"文件夹对了、文件名随意"，于是学生把 `g03.cpp` 放进 `s03/` 就会被错配到
    第 2 题、同时第 3 题反而找不到文件。现在**文件名属于别的题时第 2 条不算命中**，并把原因
    写进 missing（"s03/ 里的 g03.cpp 是第 3 题（g03）的代码"）；第 3 条仍然按文件名认，
    所以那个文件会正确地落到第 3 题。

    `strict=True`（**默认**，真实考场口径，见 `STRICT_CASE`）：文件名只差大小写
    （`M02.cpp` vs `m02`）不算命中，并在 missing 里说明；`strict=False` 放宽成老口径，
    `M02` 与 `m02` 能匹配上（测试里会用）。

    传了 `student_name` 时，还会顺带检查广东要求的个人信息文件（`<姓名>.txt`，不判分），
    没交会在 missing 末尾留一条记录；只看"交了没"用 `find_person_file()`。

    返回 (picked, missing)：picked = {题号: 相对路径}，missing = 没找到代码/需要提醒的说明。
    """
    import os.path as _p
    from . import store as _store          # 放函数里，避免模块级循环 import

    strict = strict_case() if strict is None else bool(strict)

    srcs: list[str] = []
    for f in files or []:
        name = _rel(f)
        if name and _p.splitext(name)[1].lower() in SOURCE_EXTS:
            srcs.append(name)

    # 本场的 [(题号, 英文名)]，英文名由 code_of 统一给（兜底 p{题号}）
    codes: list[tuple[int, str]] = []
    for prob in problems or []:
        no = int(prob.get("no", 0)) if isinstance(prob, dict) else int(prob)
        slug = str(_store.slug_of(prob)) if isinstance(prob, dict) else f"p{no}"
        codes.append((no, slug))

    def stem_of(path: str, exact: bool) -> str:
        base = _p.splitext(_p.basename(path))[0]
        return base if exact else base.lower()

    def dirs_of(path: str, exact: bool) -> list[str]:
        ds = [_p.basename(p) for p in _p.dirname(path).split("/") if p and p != "."]
        return ds if exact else [d.lower() for d in ds]

    def reject_why(path: str, cur_no: int, exact: bool):
        """第 2 条的把关：文件夹对了，但文件名是不是"写错了"？

        返回 ("other", 题号, 编号) —— 这个文件名属于第 N 题；
             ("case", 0, "")        —— 文件名与本场某题的编号只差大小写（严格模式才算写错）；
             None                   —— 名字没毛病，按"文件名随意"认下来。
        """
        stem = stem_of(path, exact)
        low = stem.lower()
        for q_no, q_slug in codes:
            if q_slug.lower() != low:
                continue
            if stem == q_slug:                       # 大小写完全对得上
                if q_no != cur_no:
                    return ("other", q_no, q_slug)
                continue                             # 就是本题的名字，不该在这里被拦
            if not exact:                            # 宽松模式：大小写不同不算错
                if q_no != cur_no:
                    return ("other", q_no, q_slug)
                continue
            if q_no == cur_no:                       # 严格模式：本题的名字写错了大小写
                return ("case", 0, "")
            return ("other", q_no, q_slug)           # 严格模式：别的题的名字写错了大小写
        return None

    def ladder(free: list[str], no: int, slug: str, exact: bool):
        """按 1..5 的顺序给某题挑一个文件，返回 (选中的文件, 被否掉的原因列表)。"""
        low = slug if exact else slug.lower()
        in_dir = [p for p in free if low in dirs_of(p, exact)]
        # 1) 标准结构：<编号> 目录下的同名文件
        same_dir = [p for p in in_dir if stem_of(p, exact) == low]
        if same_dir:
            return same_dir[0], []
        # 2) 文件夹对了、文件名不属于别的题
        rejects: list[tuple[str, tuple]] = []
        for p in in_dir:
            why = reject_why(p, no, exact)
            if why:
                rejects.append((p, why))
            else:
                return p, rejects
        # 3) 同名文件（任何目录）：没建文件夹，或文件名比文件夹更可信
        same_any = [p for p in free if stem_of(p, exact) == low]
        if same_any:
            return same_any[0], rejects
        # 4) 以编号开头的唯一文件（candy_brute.cpp 之类）
        pref = [p for p in free
                if stem_of(p, exact).startswith(low) and not reject_why(p, no, exact)]
        if len(pref) == 1:
            return pref[0], rejects
        # 5) 兜底：只有一道题、也只交了一个文件时就用它（"瞎放文件"的容错）
        if len(codes) == 1 and len(free) == 1 and not reject_why(free[0], no, exact):
            return free[0], rejects
        return "", rejects

    picked: dict[int, str] = {}
    missing: list[str] = []
    used: set[str] = set()
    for no, slug in codes:
        free = [p for p in srcs if p not in used]
        hit, rejects = ladder(free, no, slug, strict)
        if strict_layout:
            allowed = {f"{slug}/{slug}.cpp"}
            if kaohao:
                allowed.add(f"{kaohao}/{slug}/{slug}.cpp")
            hit = next((p for p in free if p in allowed), "")
        if hit:
            picked[no] = hit
            used.add(hit)
            continue
        msg = (f"第 {no} 题（{slug}）：没找到代码文件，"
               f"应放在 {slug}/ 文件夹里、命名为 {slug}.cpp")
        notes: list[str] = []
        for p, why in rejects:
            if why[0] == "other":
                notes.append(f"{p} 这个文件名属于第 {why[1]} 题（{why[2]}），本题不采用"
                             f"——该文件请放进 {why[2]}/ 文件夹")
            else:
                notes.append(f"{p} 的大小写与题目英文名 {slug} 不一致"
                             f"（严格模式区分大小写，应为 {slug}/ 与 {slug}.cpp），已忽略")
        if not rejects and strict_layout:
            notes.append(f"CSP 只采用 {slug}/{slug}.cpp（可置于本考号目录下），其它位置或扩展名不计分")
        elif not rejects and strict:
            probe, _ = ladder(free, no, slug, False)      # 只为出提示，不参与判分
            if probe:
                notes.append(f"{probe} 的大小写与题目英文名 {slug} 不一致"
                             f"（严格模式区分大小写，应为 {slug}/ 与 {slug}.cpp），已忽略")
        if notes:
            msg += "；" + "；".join(notes)
        missing.append(msg)

    hint = person_file_hint(files, student_name, strict=strict)
    if hint:
        missing.append(hint)
    return picked, missing


def supports_source(ext: str) -> bool:
    """是否支持编译该源码后缀。"""
    return ext.lower() in SUPPORTED_EXTS


def csp_source_violation(source) -> str:
    """检查常见官方禁用写法；忽略注释、普通字符串和原始字符串。

    这不是完整 C++ 静态分析器，不能替代教师对规避规则代码的复核。
    """
    import re
    text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
    text = text.replace("\\\r\n", "").replace("\\\n", "")
    literals = re.compile(r'(?:u8|u|U|L)?R"(?P<delim>[^ ()\\\t\r\n]{0,16})\(.*?\)(?P=delim)"'
                          r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)
    def mask(m):
        if m.group(0).startswith('"') and re.search(r'\b_Pragma\s*\(\s*$', text[max(0, m.start()-100):m.start()]):
            return m.group(0)
        return " " + "\n" * m.group(0).count("\n")
    code = literals.sub(mask, text)
    forbidden = r'(?:GCC\s+(?:optimize|target|push_options|pop_options|reset_options)|clang\s+optimize)\b'
    if re.search(r'^\s*#\s*pragma\s+' + forbidden, code, re.M) or re.search(r'\b_Pragma\s*\(\s*"\s*' + forbidden, code):
        return "源代码改变编译器参数（pragma），违反 CSP 官方要求"
    if re.search(r'\b(?:asm|__asm__|__asm)\s*(?:(?:volatile|__volatile__)\s*)?\(', code):
        return "源代码使用内联汇编，违反 CSP 官方要求"
    if re.search(r'\b__attribute__\s*\(\s*\(\s*(?:optimize|target)\b', code):
        return "源代码通过属性改变编译器参数，违反 CSP 官方要求"
    return ""


def check_freopen(source: str, base: str) -> str:
    """检查选手代码里有没有正确的 freopen 调用，返回提示（没问题则返回空串）。

    这只是提示，不改写源码；fstream 等其它文件 I/O 同样可用。
    """
    import re
    pat = re.compile(r'freopen\s*\(\s*"([^"]+)"', re.I)
    found = pat.findall(source)
    want_in = f"{base}.in"
    if not found:
        return "未检测到 freopen 调用；若使用 fstream 等文件 I/O 仍可正常评测，以实际生成的答案文件为准。"
    if want_in not in found:
        return (f"freopen 打开的文件是 {found[0]}，本题应该打开 {want_in}"
                f"（题目英文名 {base}）——真实 CSP 中文件名写错同样拿 0 分。")
    return ""
