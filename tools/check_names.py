"""检查"用了但没定义"的名字。

为什么需要它：`tools/lint.py` 只查未使用导入、重复 key 之类，查不出
`NameError: name '_extract_braced' is not defined` 这种漏 import ——
而这种错误只有真的跑到那条分支才会炸，测试没覆盖就等于交付了个炸弹
（拆 douyin_adapter 时就踩过：真实解析一跑就崩，自检才发现）。

做法：导入每个模块，拿它的 globals，再把里面所有 code object 的
LOAD_GLOBAL 指令翻出来，跟 globals + builtins 对一遍。
比纯静态分析准（不误报闭包变量），也不需要装 pyflakes。

    python tools/check_names.py
"""

from __future__ import annotations

import builtins
import dis
import importlib
import os
import pkgutil
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iter_code(obj: object, owner: str):
    """递归挖出对象里所有的 code object。

    `owner` 是"应该属于哪个模块"：包的 __init__ 会 re-export 子模块的函数，
    那些函数的 globals 在子模块里，拿包的 globals 去比全是误报。
    """
    if isinstance(obj, types.FunctionType):
        # 直接过滤，避免把子模块函数当成自己的
        if obj.__module__ == owner:
            yield obj.__code__
    elif isinstance(obj, types.MethodType):
        yield from iter_code(obj.__func__, owner)
    elif isinstance(obj, (staticmethod, classmethod)):
        yield from iter_code(obj.__func__, owner)
    elif isinstance(obj, type):
        if obj.__module__ == owner:
            for value in vars(obj).values():
                yield from iter_code(value, owner)
    elif isinstance(obj, property):
        for fn in (obj.fget, obj.fset, obj.fdel):
            if fn is not None:
                yield from iter_code(fn, owner)
    elif isinstance(obj, types.CodeType):
        yield obj


def nested(code: types.CodeType):
    """code object 里嵌套的函数 / 推导式。"""
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield const
            yield from nested(const)


def check_module(mod: types.ModuleType, pkg_dir: str) -> list[str]:
    problems = []
    known = set(vars(mod)) | set(dir(builtins))

    codes: list[types.CodeType] = []
    for value in list(vars(mod).values()):
        codes.extend(iter_code(value, mod.__name__))

    seen: set[int] = set()
    queue = list(codes)
    while queue:
        code = queue.pop()
        if id(code) in seen:
            continue
        seen.add(id(code))
        # 只查本项目自己的代码：模块命名空间里还有导入进来的 dataclasses /
        # shutil / PySide6 的函数，它们的 LOAD_GLOBAL 用的是各自模块的 globals，
        # 拿这个模块的 globals 去比，全是误报。
        try:
            fname = os.path.abspath(code.co_filename)
        except (OSError, ValueError):
            fname = ""
        own = fname.startswith(pkg_dir)
        if own:
            for instr in dis.get_instructions(code):
                if instr.opname not in ("LOAD_GLOBAL", "LOAD_NAME"):
                    continue
                name = instr.argval
                if name in known:
                    continue
                problems.append(f"{mod.__name__}:{code.co_name} → {name}")
        queue.extend(nested(code))
    return problems


def main() -> int:
    sys.path.insert(0, ROOT)
    import mediagrab

    pkg_dir = os.path.abspath(os.path.dirname(mediagrab.__file__))
    problems: list[str] = []
    failed: list[str] = []
    for info in pkgutil.walk_packages(mediagrab.__path__, "mediagrab."):
        try:
            mod = importlib.import_module(info.name)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{info.name}: {type(e).__name__}: {e}")
            continue
        problems.extend(check_module(mod, pkg_dir))

    if failed:
        print("导入失败：")
        for line in failed:
            print("  " + line)
    if problems:
        print("未定义的名字：")
        for line in problems:
            print("  " + line)
        return 1
    if not failed:
        print("无问题")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
