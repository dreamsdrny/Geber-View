"""Layer-aware, bounded-memory raster comparison.

Coordinates and tolerance are in millimetres.  Each tile is independent, so a
connected difference crossing a tile boundary can be reported more than once.
"""
import math
import os
from collections import defaultdict

from PIL import ImageChops


TILE_SIZE = 512
MAX_PIXELS = 100_000_000
MAX_CLUSTERS = 100_000


def _dilate_mm(image, resolution, tolerance):
    """Euclidean pixel-centre tolerance; do not round a small tolerance up."""
    radius = int(math.floor(tolerance / resolution + 1e-12))
    if not radius:
        return image
    result = image.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if (dx or dy) and math.hypot(dx, dy) * resolution <= tolerance + 1e-12:
                result = ImageChops.lighter(result, ImageChops.offset(image, dx, dy))
    return result


def layer_key(layer):
    role = layer.role or {}
    kind = "drill" if layer.kind == "drill" else role.get("kind") or layer.kind
    if kind == "other":
        return ("other", os.path.basename(layer.data.get("file") or layer.name).lower())
    if kind == "drill":
        plating = role.get("plated")
        if plating is None:
            source = str(layer.data.get("plating") or "").upper()
            plating = True if source == "PTH" else False if source == "NPTH" else None
        via = bool(role.get("via", False))
        name = os.path.basename(layer.data.get("file") or layer.name).lower() if via else ""
        return (kind, str(plating), str(via), name)
    return (kind, role.get("side") or "", str(role.get("index") or ""))


def pair_layers(a, b):
    groups = defaultdict(lambda: [[], []])
    for side, layers in enumerate((a, b)):
        for layer in layers:
            groups[layer_key(layer)][side].append(layer)
    pairs, notes = [], []
    for key, (left, right) in sorted(groups.items()):
        # Exact names first. Ambiguous duplicates are paired deterministically,
        # with an explicit note so the result can be reviewed by the user.
        name = lambda l: os.path.basename(l.data.get("file") or l.name).lower()
        left.sort(key=name)
        right.sort(key=name)
        if len(left) > 1 or len(right) > 1:
            notes.append("同类型多层先按同名、再按文件名排序配对: " + "/".join(key))
        matched = []
        remaining = list(right)
        unmatched = []
        for la in left:
            match = next((lb for lb in remaining if name(la) == name(lb)), None)
            if match is None:
                unmatched.append(la)
            else:
                matched.append((la, match))
                remaining.remove(match)
        for i in range(max(len(unmatched), len(remaining))):
            matched.append((unmatched[i] if i < len(unmatched) else None,
                            remaining[i] if i < len(remaining) else None))
        for la, lb in matched:
            label = "/".join(key) + "  " + (la.name if la else "(缺A)") + " ↔ " + (lb.name if lb else "(缺B)")
            if la is None or lb is None:
                notes.append("缺少对应图层: " + label)
            pairs.append((label, la, lb))
    return pairs, notes


def _components(image, bbox, scale, side, label):
    """Extract 4-connected runs from a tile, without allocating Python objects per pixel."""
    w, h = image.size
    pixels = bytearray(image.tobytes())
    points = []
    for pos, value in enumerate(pixels):
        if not value:
            continue
        stack = [pos]
        pixels[pos] = 0
        count = sx = sy = 0
        x0 = w; x1 = y0 = y1 = 0
        y0 = h
        while stack:
            i = stack.pop()
            y, x = divmod(i, w)
            count += 1; sx += x; sy += y
            x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
            for n in (i - 1 if x else -1, i + 1 if x + 1 < w else -1,
                      i - w if y else -1, i + w if y + 1 < h else -1):
                if n >= 0 and pixels[n]:
                    pixels[n] = 0
                    stack.append(n)
        minx, _miny, _maxx, maxy = bbox
        bx0 = minx + x0 / scale; bx1 = minx + (x1 + 1) / scale
        by0 = maxy - (y1 + 1) / scale; by1 = maxy - y0 / scale
        points.append({"x": minx + (sx / count + 0.5) / scale,
                       "y": maxy - (sy / count + 0.5) / scale,
                       "side": side, "area": count / (scale * scale), "cells": count,
                       "ext": max(bx1 - bx0, by1 - by0), "layer": label,
                       "bx0": bx0, "bx1": bx1, "by0": by0, "by1": by1})
    return points


def compare(pairs, bbox, resolution, tolerance, render, cancelled=lambda: False):
    if not math.isfinite(resolution) or resolution <= 0 or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("精度必须为正数，容差必须非负")
    scale = 1.0 / resolution
    minx, miny, maxx, maxy = bbox
    width = max(1, math.ceil((maxx - minx) * scale))
    height = max(1, math.ceil((maxy - miny) * scale))
    if width * height * max(1, len(pairs)) > MAX_PIXELS:
        raise ValueError("对比像素总量超过上限，请增大每像素毫米数或减少图层")
    radius = int(math.floor(tolerance * scale + 1e-12))
    if radius > 8:
        raise ValueError("容差不能大于 8 个像素，请调整精度或容差")
    points = []
    for label, la, lb in pairs:
        for py in range(0, height, TILE_SIZE):
            for px in range(0, width, TILE_SIZE):
                if cancelled():
                    raise InterruptedError("对比已取消")
                tw = min(TILE_SIZE, width - px); th = min(TILE_SIZE, height - py)
                # Extend each tile so tolerance also works across tile boundaries.
                x0 = minx + (px - radius) / scale
                y1 = maxy - (py - radius) / scale
                tile_bbox = (x0, y1 - (th + 2 * radius) / scale,
                             x0 + (tw + 2 * radius) / scale, y1)
                a = render(la, tile_bbox, scale)
                b = render(lb, tile_bbox, scale)
                if radius:
                    aa = _dilate_mm(a, resolution, tolerance)
                    bb = _dilate_mm(b, resolution, tolerance)
                else:
                    aa, bb = a, b
                crop = (radius, radius, radius + tw, radius + th)
                da = ImageChops.subtract(a, bb).crop(crop)
                db = ImageChops.subtract(b, aa).crop(crop)
                core_bbox = (minx + px / scale, maxy - (py + th) / scale,
                             minx + (px + tw) / scale, maxy - py / scale)
                for mask, side in ((da, "A"), (db, "B")):
                    if mask.getbbox():
                        points.extend(_components(mask, core_bbox, scale, side, label))
                if len(points) > MAX_CLUSTERS:
                    raise ValueError("差异数量超过上限，请增大像素尺寸或缩小对比范围")
    points.sort(key=lambda p: p["area"], reverse=True)
    return points
