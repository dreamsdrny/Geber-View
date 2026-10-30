# -*- coding: utf-8 -*-
"""新旧解析器逐字节等价性对拍

用法:
    python -B tools/eq_check.py            # 对比 _ref_reader.py 与 gerber_reader.py
    python -B tools/eq_check.py -v         # 打印每个文件的解析结果摘要

比较内容: 结果 dict 的全部标量/容器 + PrimStore 的完整 record 逐位(二进制相同)对比。
"""
import glob
import io
import os
import pickle
import struct
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CORPUS = os.path.join(HERE, "_corpus")
REF = os.path.join(HERE, "_ref_reader.py")


def load(name, path):
    return SourceFileLoader(name, path).load_module()


def canon(data):
    """把解析结果变成可比较的规范结构 (浮点按 IEEE-754 位模式比较)"""
    out = {}
    for k, v in data.items():
        if k == "primitives":
            continue
        out[k] = v
    st = data.get("primitives")
    if st is not None:
        out["__rec__"] = struct.pack("<%dd" % len(st.rec), *st.rec)
        out["__n__"] = st.n
        out["__extras__"] = sorted(
            (i, (tuple(map(tuple, pts)) if pts else None), net, ref, pin)
            for i, (pts, net, ref, pin) in st.extras.items())
    return out


def diff(a, b, path=""):
    """返回差异描述列表 (递归)"""
    bad = []
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return ["%s: type %s != %s" % (path, type(a).__name__, type(b).__name__)]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                bad.append("%s.%s: 仅新实现有" % (path, k))
            elif k not in b:
                bad.append("%s.%s: 仅旧实现有" % (path, k))
            else:
                bad += diff(a[k], b[k], "%s.%s" % (path, k))
        return bad
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return ["%s: 长度 %d != %d" % (path, len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            bad += diff(x, y, "%s[%d]" % (path, i))
        return bad
    if a != b:
        if isinstance(a, float):
            return ["%s: %.17g != %.17g" % (path, a, b)]
        bad.append("%s: %r != %r" % (path, a, b))
    return bad


def main(argv):
    verbose = "-v" in argv
    if not os.path.isdir(CORPUS):
        print("缺少语料库, 先运行 tools/gen_corpus.py")
        return 1
    ref = load("_ref_reader", REF)
    new = load("gerber_reader", os.path.join(ROOT, "gerber_reader.py"))
    files = sorted(glob.glob(os.path.join(CORPUS, "*.gbr")))
    if not files:
        print("语料库为空")
        return 1
    fails = 0
    for f in files:
        name = os.path.basename(f)
        try:
            a = canon(ref.parse_gerber(f))
            b = canon(new.parse_gerber(f))
        except Exception as exc:                       # noqa: BLE001
            print("[EXC ] %-22s %s: %s" % (name, type(exc).__name__, exc))
            fails += 1
            continue
        d = diff(a, b)
        if d:
            fails += 1
            print("[DIFF] %-22s %d 处差异" % (name, len(d)))
            for line in d[:8]:
                print("        ", line)
        else:
            if verbose:
                print("[ OK ] %-22s prims=%-6d bbox=%s unit=%s" % (
                    name, a.get("feature_count"), a.get("bbox"), a.get("unit")))
            else:
                print("[ OK ] %s" % name)
    print("\n%d/%d 通过" % (len(files) - fails, len(files)))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
