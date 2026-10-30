# -*- coding: utf-8 -*-
"""Rust 原生解析后端 (gerber_core.pyd) 的 Python 胶水

设计原则: **要么逐字节等价, 要么完全不用**。
Rust 只处理它能确定性复刻的子集 (标准坐标命令/简单光圈/显式单位),
遇到 %AM / %SR / 区域 / 圆弧 / X2 属性 / 未声明单位 就返回 None,
这里也返回 None, 上层 parse_gerber 自动回退到纯 Python 实现。
"""
import os
import sys

_MOD = None
_ERR = None
_LAST = None          # 最近一次 parse 失败的异常 (诊断用)


def _dirs():
    out = []
    here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        out.append(os.path.dirname(sys.executable))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(meipass)
    else:
        out.append(here)
        out.append(os.path.join(here, "rust", "gerber_core"))
    return out


def available():
    """尝试加载原生模块, 返回是否可用 (结果缓存)"""
    global _MOD, _ERR
    if _MOD is not None:
        return True
    if _ERR is not None:
        return False
    if os.environ.get("GERBER_NO_NATIVE") == "1":
        _ERR = "disabled by GERBER_NO_NATIVE"
        return False
    for d in _dirs():
        for name in ("gerber_core.pyd", "gerber_core.dll"):
            p = os.path.join(d, name)
            if not os.path.exists(p):
                continue
            try:
                if d not in sys.path:
                    sys.path.insert(0, d)
                import gerber_core            # noqa: F401
                _MOD = gerber_core
                return True
            except Exception as exc:           # noqa: BLE001
                _ERR = "%s: %s" % (p, exc)
    if _ERR is None:
        _ERR = "gerber_core.pyd 未找到"
    return False


def status():
    return (_MOD is not None, _ERR)


def parse(path, layer_name=None):
    """原生解析; 不支持或出错返回 None (调用方回退纯 Python)"""
    global _LAST
    if not available():
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        r = _MOD.parse(raw)
    except Exception as exc:                   # noqa: BLE001
        _LAST = "%s: %s" % (type(exc).__name__, exc)
        return None
    if r is None:
        _LAST = "unsupported by rust core"
        return None
    try:
        return _build(path, layer_name, r)
    except Exception as exc:                   # noqa: BLE001
        _LAST = "build: %s: %s" % (type(exc).__name__, exc)
        return None


def _build(path, layer_name, r):
    import gerber_reader as G

    res = G.GerberResult()
    res.file_name = layer_name or os.path.basename(path)
    st = res.primitives
    st.load_records(r["rec"], r["n"])
    res.feature_count = r["feat"]
    res.min_width = r["minw"]
    if r["has_bbox"]:
        res.bbox = [float(v) for v in r["bbox"]]
    # 区域顶点 -> extras; 圆弧包围盒采样交给 Python (保证与纯 Python 位级一致)
    for idx, flat in r["regions"]:
        pts = [(flat[k], flat[k + 1]) for k in range(0, len(flat), 2)]
        st.extras[int(idx)] = (pts, "", "", "")
    for a in r["arcs"]:
        for px, py in G._arc_sample(a[0], a[1], a[2], a[3], a[4], a[5],
                                    bool(a[6]), 12):
            res.add_point(px, py)
    for code, txt in r["aps"]:
        got = G._parse_aperture(txt)
        if got:
            res.apertures[got[0]] = got[1]
    for code, cnt in r["ap_use"]:
        res.ap_usage[code] = cnt

    unit = r["unit"]
    unit_src = r["unit_src"]
    res.unit = unit
    res.unit_source = unit_src
    scale = [float(r["scale"][0]), float(r["scale"][1])]
    off = [float(r["off"][0]), float(r["off"][1])]
    mirror = [bool(r["mirror"][0]), bool(r["mirror"][1])]
    rot = int(r["rot"])
    int_d = int(r["int_d"])
    zero = r["zero"]

    warnings = []
    if r["has_span"]:
        max_abs = max([abs(float(v)) for v in r["span"]] or [0.0])
        if max_abs >= 10 ** int_d:
            warnings.append(
                "坐标超出 %%FS 声明的整数位(%d位)范围, 格式声明可能与数据不符" % int_d)
    if zero == "T":
        warnings.append("坐标为尾随零省略(FSTA)")
    if scale != [1.0, 1.0]:
        warnings.append("含图像比例 %%SF=%.4f/%.4f, 已按比例还原" % (scale[0], scale[1]))
    if any(mirror):
        warnings.append("含图像镜像 %%MI=%s" % ("A" if mirror[0] else "") +
                        ("B" if mirror[1] else ""))
    if rot:
        warnings.append("含图像旋转 %%IR%d" % rot)
    # 与纯 Python 路径末尾的图幅合理性检查保持一致
    b = res.bbox
    if b and b[0] is not None:
        w, h = b[2] - b[0], b[3] - b[1]
        if max(w, h) > 1200:
            warnings.append(
                "图幅 %.0f×%.0fmm 异常偏大, 请确认单位/比例声明 (可能为图纸层或拼版)" % (w, h))
        elif max(w, h) < 1:
            warnings.append(
                "图幅仅 %.2f×%.2fmm 异常偏小, 请确认坐标格式与单位声明" % (w, h))

    return {
        "kind": "gerber",
        "file": path,
        "name": res.file_name,
        "unit": unit,
        "unit_source": unit_src,
        "format": "%d.%d" % (int_d, int(r["dec_d"])),
        "zero_mode": zero,
        "coord_mode": r["coord"],
        "software": res.software,
        "creation_date": res.creation_date,
        "file_polarity": res.file_polarity,
        "image_scale": scale,
        "image_offset": off,
        "image_mirror": mirror,
        "image_rotation": rot,
        "sr_steps": 0,
        "macro_count": 0,
        "apertures": res.apertures,
        "primitives": res.primitives,
        "bbox": res.bbox,
        "feature_count": res.feature_count,
        "attributes_present": False,
        "nets": {},
        "min_line_width_mm": round(res.min_width, 4) if res.min_width else None,
        "min_pad_mm": G._min_pad(res),
        "aperture_count": len(res.apertures),
        "file_function": res.file_function,
        "file_part": res.file_part,
        "aperture_function": res.aperture_function,
        "warnings": warnings,
    }
