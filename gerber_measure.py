"""Millimetre geometry for picking and object-to-object measurements.

Circles, rectangles, obrounds, round tracks and slots are analytic. Arcs and
aperture holes use polylines with a target chord error of 0.001 mm.
No screen/raster coordinates are used for distance calculations.
"""
import math
from dataclasses import dataclass

CHORD_ERROR = .001


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def project(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = max(0., min(1., ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2)) if length2 else 0.
    return a[0] + t * dx, a[1] + t * dy


def segments_nearest(a, b, c, d):
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = d[0] - c[0], d[1] - c[1]
    cross = ux * vy - uy * vx
    if abs(cross) > 1e-15:
        wx, wy = c[0] - a[0], c[1] - a[1]
        t = (wx * vy - wy * vx) / cross
        s = (wx * uy - wy * ux) / cross
        if 0 <= t <= 1 and 0 <= s <= 1:
            p = a[0] + t * ux, a[1] + t * uy
            return p, p
    return min(((a, project(a, c, d)), (b, project(b, c, d)),
                (project(c, a, b), c), (project(d, a, b), d)),
               key=lambda pair: distance(*pair))


def circle_points(center, radius):
    if radius <= 0:
        return [center]
    step = 2 * math.acos(max(-1., min(1., 1 - CHORD_ERROR / radius)))
    n = max(12, min(4096, math.ceil(2 * math.pi / max(step, 1e-6))))
    return [(center[0] + radius * math.cos(k * 2 * math.pi / n),
             center[1] + radius * math.sin(k * 2 * math.pi / n)) for k in range(n)]


@dataclass
class Feature:
    key: tuple
    center: tuple
    paths: list
    radius: float = 0.
    filled: bool = False
    label: str = ""
    approximate: bool = False

    def segments(self):
        for path in self.paths:
            if len(path) == 1:
                yield path[0], path[0]
            else:
                yield from zip(path, path[1:])
                if self.filled and path[-1] != path[0]:
                    yield path[-1], path[0]

    def contains(self, p):
        if not self.filled:
            return min((distance(p, project(p, a, b)) for a, b in self.segments()),
                       default=math.inf) <= self.radius + 1e-12
        inside = False
        for a, b in self.segments():
            if distance(p, project(p, a, b)) < 1e-12:
                return True
            if (a[1] > p[1]) != (b[1] > p[1]):
                if p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                    inside = not inside
        return inside

    def boundary(self, p):
        q = min((project(p, a, b) for a, b in self.segments()), key=lambda q: distance(p, q))
        if not self.radius:
            return q
        dx, dy = p[0] - q[0], p[1] - q[1]
        n = math.hypot(dx, dy)
        if n < 1e-12:
            # Cursor on centreline: choose a normal, not a point along the axis.
            a, b = min(self.segments(), key=lambda ab: distance(q, project(q, *ab)))
            dx, dy = -(b[1] - a[1]), b[0] - a[0]
            n = math.hypot(dx, dy)
            if n < 1e-12:
                dx, dy, n = 1., 0., 1.
        return q[0] + dx / n * self.radius, q[1] + dy / n * self.radius

    def pick_distance(self, p):
        return 0. if self.contains(p) else distance(p, self.boundary(p))


def nearest_features(a, b):
    """Return shortest clearance endpoints; touching/overlapping gives zero."""
    pa, pb = min((segments_nearest(x, y, u, v)
                  for x, y in a.segments() for u, v in b.segments()),
                 key=lambda pair: distance(*pair))
    if a.contains(pb):
        return pb, pb
    if b.contains(pa):
        return pa, pa
    n = distance(pa, pb)
    if n <= a.radius + b.radius + 1e-12:
        t = min(a.radius, n)
        p = (pa[0] + (pb[0] - pa[0]) * t / n,
             pa[1] + (pb[1] - pa[1]) * t / n) if n else pa
        return p, p
    dx, dy = (pb[0] - pa[0]) / n, (pb[1] - pa[1]) / n
    return ((pa[0] + a.radius * dx, pa[1] + a.radius * dy),
            (pb[0] - b.radius * dx, pb[1] - b.radius * dy))


def primitive_feature(viewer, layer, i):
    from gerber_reader import _contours
    store = layer.data["primitives"]
    rec, b = store.rec, i * 9
    code = int(rec[b]); kind = code & 7
    if kind == 4:
        return None
    p = (rec[b + 1], rec[b + 2]); q = (rec[b + 3], rec[b + 4])
    radius = max(0., rec[b + 5]) / 2
    center = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    paths, filled, approx = [[p, q]], False, False
    label = "走线（线段中点）"
    if kind == 0:
        ap = (layer.data.get("apertures") or {}).get(int(rec[b + 6])) or {}
        if ap.get("shape", "C") != "C":
            label, approx = "非圆光圈走线（等效线宽近似）", True
    if kind == 1:
        center = p
        shape, w, h, edges, rot = viewer._ap_shape_data(layer.data, int(rec[b + 6]), rec[b + 5])
        label = f"焊盘 {shape} {w:.4f}×{h:.4f} mm"
        if shape == "R":
            paths = [[(p[0] - w / 2, p[1] - h / 2), (p[0] + w / 2, p[1] - h / 2),
                      (p[0] + w / 2, p[1] + h / 2), (p[0] - w / 2, p[1] + h / 2)]]
            filled, radius = True, 0.
        elif shape == "P" and edges >= 3:
            paths = [[(p[0] + w / 2 * math.cos(math.radians(rot + k * 360 / edges)),
                       p[1] + w / 2 * math.sin(math.radians(rot + k * 360 / edges))) for k in range(edges)]]
            filled, radius = True, 0.
        elif shape == "O":
            radius = min(w, h) / 2
            dx, dy = max(0., (w - h) / 2), max(0., (h - w) / 2)
            paths = [[(p[0] - dx, p[1] - dy), (p[0] + dx, p[1] + dy)]]
        else:
            radius, paths = w / 2, [[p]]
        aperture = (layer.data.get("apertures") or {}).get(int(rec[b + 6])) or {}
        params = aperture.get("params") or []
        hole_index = {"C": 1, "R": 2, "O": 2, "P": 3}.get(shape)
        hole = params[hole_index] if hole_index is not None and len(params) > hole_index else 0
        if isinstance(hole, (float, int)) and hole > 0:
            if radius:
                # Convert round/obround outer boundary into a filled contour.
                a, z = paths[0][0], paths[0][-1]
                if a == z:
                    outer = circle_points(a, radius)
                else:
                    theta = math.atan2(z[1] - a[1], z[0] - a[0])
                    n = len(circle_points(a, radius))
                    outer = [(c[0] + radius * math.cos(theta + angle),
                              c[1] + radius * math.sin(theta + angle))
                             for c, start in ((z, -math.pi / 2), (a, math.pi / 2))
                             for angle in (start + k * math.pi / n for k in range(n + 1))]
                paths = [outer]
            hole *= 25.4 if layer.data.get("unit") == "inch" else 1.
            paths.append(circle_points(p, hole / 2))
            radius, filled, approx = 0., True, True
    elif kind == 2:
        r = math.hypot(rec[b + 7], rec[b + 8])
        step = 2 * math.acos(max(-1., min(1., 1 - CHORD_ERROR / max(r, CHORD_ERROR))))
        n = max(12, min(4096, math.ceil(2 * math.pi / max(step, 1e-6))))
        paths = [viewer._arc_points(*p, *q, rec[b + 7], rec[b + 8], bool(code & 16), seg=n)]
        center = paths[0][len(paths[0]) // 2]
        label, approx = "圆弧（路径中点，折线近似）", True
    elif kind == 3:
        extra = store.extras.get(i)
        if not extra or not extra[0]:
            return None
        paths = [list(c) for c in _contours(extra[0]) if c]
        flat = [p for path in paths for p in path]
        if not flat:
            return None
        center = ((min(p[0] for p in flat) + max(p[0] for p in flat)) / 2,
                  (min(p[1] for p in flat) + max(p[1] for p in flat)) / 2)
        filled, radius, label = True, 0., "区域（包围盒中心）"
    ox, oy = layer.offset
    def world(p):
        return ((-p[0] if layer.mirror_x else p[0]) + ox, p[1] + oy)
    return Feature((id(layer), "prim", i), world(center),
                   [[world(p) for p in path] for path in paths], radius, filled,
                   f"{layer.name}: {'清除轮廓 ' if code & 8 else ''}{label}", approx)


def hole_feature(layer, i):
    h = layer.data["holes"][i]
    if h.get("x") is None or h.get("y") is None or not h.get("diameter_mm"):
        return None
    ox, oy = layer.offset
    def world(x, y):
        return ((-x if layer.mirror_x else x) + ox, y + oy)
    p = world(h["x"], h["y"])
    slot = h.get("is_slot") and h.get("x2") is not None and h.get("y2") is not None
    q = world(h["x2"], h["y2"]) if slot else p
    return Feature((id(layer), "hole", i), ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2),
                   [[p, q]], h["diameter_mm"] / 2, label=f"{layer.name}: {'槽孔' if slot else '孔'} Ø{h['diameter_mm']:.4f} mm")
