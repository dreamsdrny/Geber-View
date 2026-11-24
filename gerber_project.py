"""Portable, versioned viewer project files (geometry stays in source files)."""
import json
import math
import os


VERSION = 1


def make_project(layers, path):
    base = os.path.dirname(os.path.abspath(path))
    records = []
    for layer in layers:
        source = (layer.raw_data or {}).get("file")
        if not source:
            raise ValueError("图层缺少源文件路径: " + layer.name)
        try:
            source = os.path.relpath(os.path.abspath(source), base)
        except ValueError:  # Windows different drives
            source = os.path.abspath(source)
        records.append({"path": source, "name": layer.name, "group": layer.group,
                        "color": layer.color, "visible": layer.visible,
                        "scale": layer.scale_factor, "offset": list(layer.offset),
                        "mirror_x": layer.mirror_x,
                        "role_override": getattr(layer, "role_override", None),
                        "parse_options": getattr(layer, "parse_options", {})})
    return {"version": VERSION, "layers": records}


def save(path, layers):
    data = make_project(layers, path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("layers"), list):
        raise ValueError("无法识别工程格式或版本")
    if len(data["layers"]) > 2000:
        raise ValueError("工程图层数量超过上限")
    base = os.path.dirname(os.path.abspath(path))
    result = []
    for item in data["layers"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("工程图层路径无效")
        factor = float(item.get("scale", 1))
        offset = item.get("offset", [0, 0])
        if not math.isfinite(factor) or factor <= 0 or not isinstance(offset, list) or len(offset) != 2:
            raise ValueError("工程图层比例或偏移无效")
        offset = [float(v) for v in offset]
        if not all(math.isfinite(v) for v in offset):
            raise ValueError("工程图层偏移无效")
        group = item.get("group", "")
        if group not in ("", "A", "B"):
            raise ValueError("工程图层分组无效")
        options = item.get("parse_options", {})
        allowed = {"unit_override", "format_override", "zero_mode_override", "aperture_override"}
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValueError("工程 Gerber 解析参数无效")
        if options:
            from gerber_reader import parse_aperture_table
            unit = options.get("unit_override")
            zero = options.get("zero_mode_override")
            digits = options.get("format_override")
            table = options.get("aperture_override")
            if unit not in (None, "mm", "inch") or zero not in (None, "L", "T"):
                raise ValueError("工程 Gerber 单位/零省略参数无效")
            if digits is not None and (not isinstance(digits, list) or len(digits) != 2 or
                    not all(type(v) is int for v in digits) or not (1 <= digits[0] <= 7 and 0 <= digits[1] <= 7)):
                raise ValueError("工程 Gerber 坐标格式无效")
            if table is not None:
                if not isinstance(table, dict) or len(table) > 10000:
                    raise ValueError("工程光圈表无效")
                try:
                    lines = [f"D{code} {ap['shape']} " + " ".join(str(v) for v in ap["params"])
                             for code, ap in table.items()]
                    options = dict(options, aperture_override=parse_aperture_table("\n".join(lines)))
                except (KeyError, TypeError):
                    raise ValueError("工程光圈表无效") from None
        override = item.get("role_override")
        if override is not None:
            kinds = {"copper", "soldermask", "silkscreen", "paste", "profile", "drill", "other"}
            sides = {"", "top", "bottom", "inner"}
            idx = override.get("index") if isinstance(override, dict) else None
            if (not isinstance(override, dict)
                    or override.get("kind") not in kinds
                    or override.get("side", "") not in sides
                    or not (idx is None or (type(idx) is int and idx >= 0))):
                raise ValueError("工程图层类型覆盖无效")
            override = {"kind": override["kind"], "side": override.get("side", ""), "index": idx}
        record = dict(item, path=os.path.abspath(os.path.join(base, item["path"])),
                      scale=factor, offset=offset, group=group, parse_options=options,
                      role_override=override)
        result.append(record)
    return result
