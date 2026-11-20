"""轻量静态检查：未使用导入、裸 except、可疑空实现、超长函数/文件。

pyflakes 装不上（离线环境），这里用 AST 覆盖最常出问题的几类。
不做完整类型分析，只标"值得人看一眼"的地方。

两类豁免：
  · 行尾 `# noqa` —— 表示已经人工确认过
  · `tools/lint_baseline.txt` —— 存量问题清单（key = 文件|类型|详情|行内容），
    只用来防新增，不掩盖既有代码

跑法：python tools/lint.py [--write-baseline]
"""

import ast
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", "build", "dist", "_dist_new", "_build_new", "design"}
BASELINE_PATH = os.path.join(ROOT, "tools", "lint_baseline.txt")
# 只对「人工审过的存量问题」做基线，长文件/长函数属于要真修的，不进基线
BASELINE_KINDS = {"SILENT-EXCEPT", "BARE-EXCEPT"}


def _line_of(src, lineno):
    lines = src.splitlines()
    return lines[lineno - 1] if 0 < lineno <= len(lines) else ""


def py_files(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def used_names(tree):
    """收集所有被引用的标识符（Name / Attribute 根 / 装饰器 / __all__ 字符串）。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            n = node
            while isinstance(n, ast.Attribute):
                n = n.value
            if isinstance(n, ast.Name):
                names.add(n.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            # __all__ = ["x", "y"] 里的字符串也算被使用
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    for elt in getattr(node.value, "elts", []):
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            names.add(elt.value)
    return names


def _scope_dups(body, rel, issues, scope=""):
    """同一作用域内重名才算覆盖；跨类同名（paintEvent 之类）不算。"""
    seen = defaultdict(list)
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seen[node.name].append(node.lineno)
        elif isinstance(node, ast.ClassDef):
            _scope_dups(node.body, rel, issues, f"{scope}{node.name}.")
    for name, lines in seen.items():
        if len(lines) > 1:
            issues.append((rel, lines[1], "DUP-DEF", f"{scope}{name} 重复定义 {lines}"))


# rel -> 源码文本，基线比对要用行内容，别为了它再读一遍磁盘
_src_cache = {}


def check_file(path):
    issues = []
    rel = os.path.relpath(path, ROOT)
    try:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [(rel, e.lineno or 0, "SYNTAX", str(e), 1)]
    _src_cache[rel] = src

    names = used_names(tree)

    # 1) 未使用的导入
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name.split(".")[0]
                # from __future__ import annotations 是编译指令，不是符号引用
                if local == "annotations" and getattr(node, "module", "") == "__future__":
                    continue
                if local not in names:
                    line = src.splitlines()[node.lineno - 1] if node.lineno <= len(src.splitlines()) else ""
                    if "noqa" in line:
                        continue          # 显式豁免（re-export 之类）
                    issues.append((rel, node.lineno, "UNUSED-IMPORT", local))

    # 2) 裸 except / except Exception: pass
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            body_is_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            if node.type is None:
                issues.append((rel, node.lineno, "BARE-EXCEPT", "except:"))
            elif body_is_pass:
                t = getattr(node.type, "id", None) or getattr(node.type, "attr", None) or "?"
                # 行尾写 noqa 表示「已经人工确认过，吞掉是对的」
                src_line = _line_of(src, node.lineno)
                if "noqa" in src_line:
                    continue
                issues.append((rel, node.lineno, "SILENT-EXCEPT", f"except {t}: pass"))

    # 3) 超长函数（> 120 行）与超长文件（> 900 行）
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            size = end - node.lineno
            if size > 120 and "noqa" not in _line_of(src, node.lineno):
                issues.append((rel, node.lineno, "LONG-FUNC", f"{node.name} {size} 行"))

    total = len(src.splitlines())
    if total > 900:
        issues.append((rel, 1, "LONG-FILE", f"{total} 行"))

    # 4) 重复定义（同一作用域内才可能互相覆盖）
    _scope_dups(tree.body, rel, issues)

    # 5) TODO / FIXME / XXX
    for i, line in enumerate(src.splitlines(), 1):
        for mark in ("TODO", "FIXME", "XXX"):
            if mark in line and "#" in line.split(mark)[0]:
                issues.append((rel, i, "MARK", line.strip()[:90]))
                break

    # 同类问题在同一文件里可能出现多次（一个文件十几处 except: pass），
    # 光靠「行内容」区分不开，补一个出现序号：插代码不影响，增删同类会变
    tally = defaultdict(int)
    numbered = []
    for rel, line, kind, detail in issues:
        text = _line_of(src, line).strip()[:100]
        tally[(kind, detail, text)] += 1
        numbered.append((rel, line, kind, detail, tally[(kind, detail, text)]))
    return numbered


def issue_key(rel, kind, detail, src, lineno, occ=1):
    """基线的比对键：文件 + 类型 + 详情 + 行内容 + 同类序号。

    不用行号 —— 上面插几行代码行号就全漂了；行内容 + 序号更稳，
    只有真的增删了一处同类代码才会变。
    """
    return f"{rel}|{kind}|{detail}|{_line_of(src, lineno).strip()[:100]}|{occ}"


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return set()
    keys = set()
    with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                keys.add(line)
    return keys


def main():
    targets = [os.path.join(ROOT, "mediagrab"), os.path.join(ROOT, "main.py"),
               os.path.join(ROOT, "tools")]
    all_issues = []
    for t in targets:
        paths = [t] if t.endswith(".py") else list(py_files(t))
        for p in paths:
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue          # 本文件里写着 TODO/FIXME 这几个词，扫自己全是误报
            all_issues.extend(check_file(p))

    baseline = load_baseline()
    kept, muted = [], 0
    for rel, line, kind, detail, occ in all_issues:
        if kind in BASELINE_KINDS:
            src = _src_cache.get(rel, "")
            if issue_key(rel, kind, detail, src, line, occ) in baseline:
                muted += 1
                continue
        kept.append((rel, line, kind, detail))

    if "--write-baseline" in sys.argv:
        rows = [issue_key(r, k, d, _src_cache.get(r, ""), l, o)
                for r, l, k, d, o in all_issues if k in BASELINE_KINDS]
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
            fh.write("# 存量静默异常清单：已人工确认「吞掉是合理的」，只防新增。\n")
            fh.write("# 重新生成：python tools/lint.py --write-baseline\n")
            for row in sorted(set(rows)):
                fh.write(row + "\n")
        print(f"已写入 {len(set(rows))} 条基线 → {BASELINE_PATH}")

    order = {"SYNTAX": 0, "DUP-DEF": 1, "BARE-EXCEPT": 2, "UNUSED-IMPORT": 3,
             "SILENT-EXCEPT": 4, "LONG-FUNC": 5, "LONG-FILE": 6, "MARK": 7}
    kept.sort(key=lambda x: (order.get(x[2], 9), x[0], x[1]))

    print(f"{'类型':<14} {'位置':<44} 详情")
    print("-" * 100)
    for rel, line, kind, detail in kept:
        print(f"{kind:<14} {rel}:{line:<36} {detail}")

    counts = defaultdict(int)
    for *_, kind, _d in kept:
        counts[kind] += 1
    print("-" * 100)
    summary = "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "无问题"
    if muted:
        summary += f"  （基线豁免 {muted} 条）"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
