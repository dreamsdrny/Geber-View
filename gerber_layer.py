"""
Gerber 图层类型识别 (Gerber viewer 精简版)
==========================================
从原 gerber_design 模块中剥离出查看器唯一需要的 identify_layer():
优先采用 Gerber X2 的 %TF.FileFunction 属性, 其次按文件名约定
(Allegro / Protel / KiCad / Eagle 等命名习惯) 判定图层类型与所在面。

返回: {"kind", "side", "index", "name", "source"[, "plated"]}
  kind : copper | soldermask | silkscreen | paste | profile | drill | other
  side : top | bottom | inner | ""
"""
import os
import re

KIND_COPPER = "copper"
KIND_MASK = "soldermask"
KIND_SILK = "silkscreen"
KIND_PASTE = "paste"
KIND_PROFILE = "profile"
KIND_DRILL = "drill"
KIND_OTHER = "other"

# 文件名约定 → (kind, side, copper_index)
NAME_RULES = [
    (r"(^|[^a-z])npth|nonplated|npt", KIND_DRILL, "", None),
    (r"\.drl$|drill|(^|[^a-z])pth|plated", KIND_DRILL, "", None),
    (r"profile|outline|boardoutline|edge\.cuts|\.gko|\.gm1|\.gm\d|route|rout", KIND_PROFILE, "", None),
    (r"soldermask|smask|sm[_\-]?top|\.gts|\-f\.mask|topmask|mask_top|smt", KIND_MASK, "top", None),
    (r"mask[_\-]?bot|\.gbs|\-b\.mask|botmask|smb", KIND_MASK, "bottom", None),
    (r"silkscreen|silk|legend|\.gto|\-f\.silks|sst|\.plc|overlay", KIND_SILK, "top", None),
    (r"silkscreen[_\-]?bot|\.gbo|\-b\.silks|ssb|\.pls", KIND_SILK, "bottom", None),
    (r"paste|stencil|\.gtp|\-f\.paste|spt", KIND_PASTE, "top", None),
    (r"paste[_\-]?bot|\.gbp|\-b\.paste|spb", KIND_PASTE, "bottom", None),
    (r"\.gtl|\-f\.cu|(^|[^a-z])top|copper[_\-]?top|\.cmp|\.art[_\-]?top", KIND_COPPER, "top", 1),
    (r"\.gbl|\-b\.cu|(^|[^a-z])bot|bottom|copper[_\-]?bot|\.sol", KIND_COPPER, "bottom", None),
    (r"\.gp\d|plane|\.g\d+$|inner|inr\d*|\-in\d+\.cu", KIND_COPPER, "inner", None),
]


def identify_layer(data: dict) -> dict:
    """识别一个 Gerber 层的类型: 优先 X2 TF.FileFunction, 其次文件名"""
    name = str(data.get("name") or os.path.basename(str(data.get("file", ""))))
    ff = str(data.get("file_function") or "").strip()
    low = name.lower()

    if ff:
        parts = [p.strip() for p in ff.split(",") if p.strip()]
        head = parts[0].lower()
        side, idx = "", None
        for p in parts[1:]:
            pl = p.lower()
            if re.fullmatch(r"l\d+", pl):
                idx = int(pl[1:])
            elif pl in ("top", "outer"):
                side = "top"
            elif pl in ("bot", "bottom"):
                side = "bottom"
            elif pl in ("inr", "inner"):
                side = "inner"
        if head == "copper":
            return {"kind": KIND_COPPER, "side": side or "inner", "index": idx,
                    "name": name, "source": "X2"}
        if head in ("soldermask", "solder_mask"):
            return {"kind": KIND_MASK, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("legend", "silkscreen", "silk"):
            return {"kind": KIND_SILK, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("solderpaste", "paste"):
            return {"kind": KIND_PASTE, "side": side, "index": None, "name": name, "source": "X2"}
        if head in ("profile", "rout", "outline", "board"):
            return {"kind": KIND_PROFILE, "side": "", "index": None, "name": name, "source": "X2"}
        if head in ("drill", "plated", "nonplated"):
            plated = "npth" not in ff.lower() and "nonplated" not in ff.lower()
            return {"kind": KIND_DRILL, "side": "", "index": None, "name": name,
                    "source": "X2", "plated": plated}

    for pat, kind, side, idx in NAME_RULES:
        if re.search(pat, low):
            return {"kind": kind, "side": side, "index": idx, "name": name, "source": "name"}
    return {"kind": KIND_OTHER, "side": "", "index": None, "name": name, "source": "unknown"}