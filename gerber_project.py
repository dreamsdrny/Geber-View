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
                        "mirror_x": layer.mirror_x})
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
        record = dict(item, path=os.path.abspath(os.path.join(base, item["path"])),
                      scale=factor, offset=offset, group=group)
        result.append(record)
    return result
