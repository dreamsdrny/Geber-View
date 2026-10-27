"""
BRD-AI: 制造数据读取与完整性检查 (Gerber / ODB++ / NC Drill)
=============================================================
支持:
  1. Gerber RS-274X 解析 (单位/格式/光圈/D01-D03/G02-G03/G36-G37/X2属性)
  2. Excellon NC Drill 解析 (孔径工具表/坐标/镀通孔判别)
  3. ODB++ (job 目录: matrix / layers/*/features / netlists/cadnet/netlist) 解析
  4. 制造数据完整性检查 (图层缺失/图形畸变风险/坐标偏移/孔径定义错误/网络属性缺失)
  5. 网络表双向比对 (CAD vs FAB -> 开路 Open / 短路 Short)

输出:
  - fab_data: 统一的几何/统计数据结构 (供 DFM 报告与图形交叉定位使用)
- issues:   与 DFM 规则一致的 DFM 问题结构 (code/severity/measured/x/y/...)
   - 网络比对报告: {net_only_cad, net_only_fab, node_open, node_short, matched, ...}

说明: 解析为 best-effort, 覆盖生产常见写法; 复杂宏/嵌套区域按近似处理。
"""
import ast
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

try:                                  # 可选依赖: 仅 DFM 完整性检查/报告功能需要
    from dfm_rules import make_issue
except Exception:                     # noqa: BLE001
    def make_issue(*_a, **_k):
        return {}

logger = logging.getLogger(__name__)

MIL_TO_MM = 0.0254
INCH_TO_MM = 25.4

INTEGRITY_ISSUE_PREFIX = "FAB"

# 板框轮廓层名识别 (Gerber/ODB 常见命名)
_OUTLINE_RE = re.compile(
    r"outline|oln|border|rout|gko|keep.?out|board.?geom|profile|mechanical|成型|板框",
    re.IGNORECASE)

_APERTURE_SHAPES = {"C", "R", "O", "P"}


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coord_value(raw: str, int_digits: int, dec_digits: int, zero_mode: str = "L") -> Optional[float]:
    """Gerber 坐标字段 -> 文件单位数值 (不做单位换算)

    兼容各家 CAM 输出:
      - %FSLA..: 省略前导零 (数值右对齐) -> 直接按小数位还原
      - %FSTA..: 省略尾随零 (数值左对齐) -> 右侧补零到 整数位+小数位
      - 直接带小数点的坐标 (少数软件) -> 原样解析
    """
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    neg = raw.startswith("-")
    digits = raw.lstrip("+-").strip()
    if "." in digits:
        val = _num(digits)
    else:
        if not digits.isdigit():
            return None
        if zero_mode == "T":
            total = int_digits + dec_digits
            digits = digits.ljust(total, "0")[:total]
        val = int(digits) / (10 ** dec_digits) if dec_digits else float(int(digits))
    if val is None:
        return None
    return -val if neg else val


def _coord_to_mm(raw: str, int_digits: int, dec_digits: int, unit: str,
                 zero_mode: str = "L") -> Optional[float]:
    val = _coord_value(raw, int_digits, dec_digits, zero_mode)
    if val is None:
        return None
    return val * (INCH_TO_MM if unit == "inch" else 1.0)


# ============================================================
# Gerber RS-274X
# ============================================================
class GerberResult:
    def __init__(self):
        self.unit = "mm"
        self.unit_source = "default"        # declared / g70 / inferred
        self.int_digits = 3
        self.dec_digits = 3
        self.zero_mode = "L"                # L=省略前导零  T=省略尾随零
        self.coord_mode = "A"               # A=绝对坐标  I=增量坐标
        self.software = ""                  # X2: %TF.GenerationSoftware
        self.creation_date = ""             # X2: %TF.CreationDate
        self.file_polarity = ""             # X2: %TF.FilePolarity
        self.image_scale = [1.0, 1.0]       # %SFA..B..
        self.image_offset = [0.0, 0.0]      # %OFA..B..  (文件单位)
        self.image_mirror = [False, False]  # %MIA..B..
        self.image_rotation = 0             # %IR0/90/180/270
        self.sr_steps = 0                   # %SR 拼版复制份数
        self.macro_count = 0
        self.warnings: List[str] = []
        self.apertures: Dict[int, dict] = {}
        self.primitives: List[dict] = []
        self.bbox = [None, None, None, None]
        self.attributes_present = False
        self.nets: Dict[str, Set[str]] = {}
        self.ap_usage: Dict[int, int] = {}
        self.feature_count = 0
        self.file_name = ""
        self.file_function = ""       # X2: %TF.FileFunction (Copper,L1,Top / SolderMask,Top / Profile ...)
        self.file_part = ""           # X2: %TF.Part
        self.aperture_function = ""   # X2: %TA.AperFunction (Conductor/Pad/Via/CopperBalancing...)

    def add_point(self, x, y):
        b = self.bbox
        if x is None or y is None:
            return
        b[0] = x if b[0] is None else min(b[0], x)
        b[1] = y if b[1] is None else min(b[1], y)
        b[2] = x if b[2] is None else max(b[2], x)
        b[3] = y if b[3] is None else max(b[3], y)


_NUM = r"[-+]?\d*\.?\d+"


def _parse_aperture(block: str) -> Optional[Tuple[int, dict]]:
    """解析 %ADD 光圈定义

    兼容: ADD10C,0.5 / ADD10R,0.5X0.6 / ADD10O,.. / ADD10P,..X6X30
          以及自定义宏 ADD21MYMACRO,<宏参数> (Cadence/Genesis/Altium 均常见)
    """
    m = re.match(r"^AD{1,2}(\d+)\s*([A-Za-z_$][\w$.\-]*)\s*,?\s*(.*)$", block.strip())
    if not m:
        return None
    dcode = int(m.group(1))
    shape = m.group(2)
    params_raw = (m.group(3) or "").strip()
    params = []
    for p in re.split(r"[Xx,]", params_raw):
        p = p.strip()
        if p == "":
            continue
        v = _num(p)
        params.append(v if v is not None else p)
    is_std = shape.upper() in ("C", "R", "O", "P")
    return dcode, {
        "shape": shape if is_std else shape.upper(),
        "params": params,
        "raw": block,
        "macro": (not is_std),
    }


# ============================================================
# Gerber 命令流分词 / 光圈宏 (AM) / 圆弧采样
# ============================================================
def _iter_gerber_commands(content: str):
    """把 Gerber 文本拆成命令流

    产出:
      ("am",  宏名, 宏体)          光圈宏定义 (可跨行)
      ("ext", 扩展子命令)          %...% 内的每一条子命令
      ("cmd", 普通命令)            以 * 结束的功能命令

    要点 (兼容不同 CAM 输出习惯):
      - Cadence Allegro 会把多条扩展命令合并进同一个 %...% 块,
        例如 %FSLAX55Y55*MOIN*% 或 %IR0*IPPOS*OFA0B0*MIA0B0*SFA1B1*%,
        必须逐条拆开解析, 否则单位(MO)/比例(SF)/偏移(OF)会被漏掉 -> 图形整体缩放错位。
      - Genesis / InCAM / Altium / KiCad 多为一条一行, 同样适用。
      - 注释 (G04) 中出现的 '%' 不会被误判为扩展块起始。
    """
    i, n = 0, len(content)
    while i < n:
        ch = content[i]
        if ch in " \t\r\n":
            i += 1
            continue
        prev = content[i - 1] if i else "\n"
        if ch == "%" and prev in "\n\r*% \t":
            j = content.find("%", i + 1)
            if j < 0:
                j = n
            block = content[i + 1:j]
            i = j + 1
            subs = block.split("*")
            k = 0
            while k < len(subs):
                s = subs[k].strip()
                k += 1
                if not s:
                    continue
                if s.upper().startswith("AM"):
                    body = []
                    while k < len(subs):
                        t = subs[k].strip()
                        k += 1
                        if t:
                            body.append(t)
                    yield ("am", s[2:].strip(), "*".join(body))
                    continue
                yield ("ext", s)
            continue
        j = content.find("*", i)
        if j < 0:
            cmd = content[i:].strip()
            i = n
        else:
            cmd = content[i:j].strip()
            i = j + 1
        if cmd:
            yield ("cmd", cmd)


def _norm_macro_expr(expr: str) -> str:
    """Gerber 宏表达式 -> 可安全求值的 Python 表达式 ($n -> _vn, x -> *, = -> ==)"""
    s = re.sub(r"\$\s*(\d+)", lambda m: "_v" + m.group(1), str(expr or ""))
    out = []
    for c in s:
        if c in "xX":
            out.append("*")
        elif c == "=":
            out.append("==")
        else:
            out.append(c)
    return "".join(out).strip()


def _macro_eval(expr, variables: Dict[str, float]) -> float:
    """受限 AST 求值 (不使用 eval), 支持 + - x / 与 = 比较"""
    if isinstance(expr, (int, float)):
        return float(expr)
    text = _norm_macro_expr(expr)
    if text == "":
        return 0.0
    try:
        node = ast.parse(text, mode="eval")
    except SyntaxError:
        v = _num(re.sub(r"[^\d.\-+]", "", text))
        return v if v is not None else 0.0

    def ev(nd) -> float:
        if isinstance(nd, ast.Expression):
            return ev(nd.body)
        if isinstance(nd, ast.Constant):
            return float(nd.value) if isinstance(nd.value, (int, float)) else 0.0
        if isinstance(nd, ast.Num):                      # py<3.8 兼容
            return float(nd.n)
        if isinstance(nd, ast.Name):
            return float(variables.get(nd.id, 0.0))
        if isinstance(nd, ast.UnaryOp):
            v = ev(nd.operand)
            return -v if isinstance(nd.op, ast.USub) else v
        if isinstance(nd, ast.BinOp):
            a, b = ev(nd.left), ev(nd.right)
            if isinstance(nd.op, ast.Add):
                return a + b
            if isinstance(nd.op, ast.Sub):
                return a - b
            if isinstance(nd.op, ast.Mult):
                return a * b
            if isinstance(nd.op, ast.Div):
                return a / b if b else 0.0
            return 0.0
        if isinstance(nd, ast.Compare):
            return 1.0 if ev(nd.left) == ev(nd.comparators[0]) else 0.0
        raise ValueError("unsupported macro expression")

    try:
        return float(ev(node))
    except Exception:                                     # noqa: BLE001
        return 0.0


def _parse_macro_body(body: str) -> List[list]:
    """解析 AM 宏体 -> [[code, arg...], ...] / [["assign", "_v1", expr], ...]"""
    prims: List[list] = []
    for chunk in re.split(r"[*\n]", body or ""):
        s = chunk.strip().rstrip(",").strip()
        if not s or s.startswith("G04"):
            continue
        am = re.match(r"^\$(\d+)\s*=\s*(.+)$", s)
        if am:
            prims.append(["assign", "_v" + am.group(1), am.group(2)])
            continue
        parts = [p.strip() for p in s.split(",")]
        code = parts[0].strip()
        if not code.lstrip("+-").isdigit():
            continue
        prims.append([int(code)] + [p for p in parts[1:] if p != ""])
    return prims


def _rot_points(points, deg: float):
    if not deg:
        return list(points)
    r = math.radians(deg)
    cs, sn = math.cos(r), math.sin(r)
    return [(x * cs - y * sn, x * sn + y * cs) for x, y in points]


def _regular_poly(cx, cy, dia, n, rot=0.0):
    n = max(3, int(n))
    r = dia / 2.0
    return [(cx + r * math.cos(math.radians(rot + k * 360.0 / n)),
             cy + r * math.sin(math.radians(rot + k * 360.0 / n))) for k in range(n)]


def _octagon_pts(cx, cy, w, h):
    """近似下左八角形 (矩形切角)"""
    cut = min(w, h) * (1 - 1 / math.sqrt(2)) / 2.0
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    return [(x0 + cut, y0), (x1 - cut, y0), (x1, y0 + cut), (x1, y1 - cut),
            (x1 - cut, y1), (x0 + cut, y1), (x0, y1 - cut), (x0, y0 + cut)]


def _expand_macro(prims: List[list], ap_params: List[float], unit_mm: float,
                  x_mm: float, y_mm: float, rotation: float = 0.0,
                  clear_default: bool = False, meta: Optional[dict] = None) -> List[dict]:
    """把光圈宏在闪装点展开为标准图元 (draw/region/flash), 坐标单位 mm

    宏内所有尺寸按【文件单位】书写, 展开后统一乘以 unit_mm 换算成 mm。
    曝光位 exposure=0 表示擦除(负片), 与图层极性叠加。
    """
    out: List[dict] = []
    variables: Dict[str, float] = {}
    for idx, p in enumerate(ap_params or []):
        if isinstance(p, (int, float)):
            variables[f"_v{idx + 1}"] = float(p)
    tr = math.radians(rotation or 0.0)
    cs, sn = math.cos(tr), math.sin(tr)

    def val(expr) -> float:
        return _macro_eval(expr, variables)

    def place(px, py):
        rx = px * cs - py * sn
        ry = px * sn + py * cs
        return x_mm + rx * unit_mm, y_mm + ry * unit_mm

    def mk(t, **kw):
        d = dict(meta or {})
        d.update(kw)
        d["type"] = t
        out.append(d)

    def clear_of(exposure) -> bool:
        return clear_default if exposure else (not clear_default)

    def poly(points, exposure):
        pts = [place(px, py) for px, py in points]
        if len(pts) >= 3:
            mk("region", points=pts, x1=pts[0][0], y1=pts[0][1],
               x2=pts[0][0], y2=pts[0][1], width=None, clear=clear_of(exposure))

    def circle(cx, cy, dia, exposure):
        px, py = place(cx, cy)
        mk("flash", x1=px, y1=py, x2=px, y2=py, aperture=None,
           width=max(abs(dia) * unit_mm, 1e-4), clear=clear_of(exposure))

    for prim in prims or []:
        if not prim:
            continue
        head = prim[0]
        if head == "assign":
            variables[prim[1]] = _macro_eval(prim[2], variables)
            continue
        code = int(head)
        a = prim[1:]
        try:
            if code == 0:
                continue
            if code == 1:                                   # 圆
                expo = val(a[0]) if a else 1
                dia = val(a[1]) if len(a) > 1 else 0.0
                cx = val(a[2]) if len(a) > 2 else 0.0
                cy = val(a[3]) if len(a) > 3 else 0.0
                circle(cx, cy, dia, expo)
            elif code in (2, 20):                           # 向量线
                expo = val(a[0]); w = val(a[1])
                sx, sy = val(a[2]), val(a[3])
                ex, ey = val(a[4]), val(a[5])
                rot = val(a[6]) if len(a) > 6 else 0.0
                p0, p1 = _rot_points([(sx, sy), (ex, ey)], rot)
                q0, q1 = place(*p0), place(*p1)
                mk("draw", x1=q0[0], y1=q0[1], x2=q1[0], y2=q1[1],
                   width=max(w * unit_mm, 1e-4), clear=clear_of(expo))
            elif code == 21:                                # 矩形 (中心)
                expo = val(a[0]); w = val(a[1]); h = val(a[2])
                cx = val(a[3]) if len(a) > 3 else 0.0
                cy = val(a[4]) if len(a) > 4 else 0.0
                rot = val(a[5]) if len(a) > 5 else 0.0
                corners = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                           (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
                poly(_rot_points(corners, rot), expo)
            elif code == 22:                                # 八角形 (下左)
                expo = val(a[0]); w = val(a[1]); h = val(a[2])
                cx = val(a[3]) if len(a) > 3 else 0.0
                cy = val(a[4]) if len(a) > 4 else 0.0
                rot = val(a[5]) if len(a) > 5 else 0.0
                poly(_rot_points(_octagon_pts(cx, cy, w, h), rot), expo)
            elif code == 4:                                 # 轮廓多边形
                expo = val(a[0]); cnt = int(val(a[1])) if len(a) > 1 else 0
                coords = a[2:]
                rot = 0.0
                need = cnt * 2
                if len(coords) > need:                      # 末尾多余的参数为旋转角
                    rot = val(coords[need])
                pts = []
                for k in range(cnt):
                    if 2 * k + 1 < len(coords):
                        pts.append((val(coords[2 * k]), val(coords[2 * k + 1])))
                if len(pts) >= 3 and pts[0] != pts[-1]:
                    pass
                poly(_rot_points(pts, rot), expo)
            elif code == 5:                                 # 正多边形
                expo = val(a[0]); cnt = val(a[1])
                cx = val(a[2]) if len(a) > 2 else 0.0
                cy = val(a[3]) if len(a) > 3 else 0.0
                dia = val(a[4]) if len(a) > 4 else 0.0
                rot = val(a[5]) if len(a) > 5 else 0.0
                poly(_regular_poly(cx, cy, dia, cnt, rot), expo)
            elif code == 6:                                 # 靶标 (同心环 + 十字)
                cx = val(a[0]) if a else 0.0
                cy = val(a[1]) if len(a) > 1 else 0.0
                outer = val(a[2]) if len(a) > 2 else 0.0
                thick = val(a[3]) if len(a) > 3 else outer * 0.1
                gap = val(a[4]) if len(a) > 4 else thick
                rings = int(val(a[5])) if len(a) > 5 else 2
                rings = max(1, min(rings, 6))
                rad = outer / 2.0
                for k in range(rings):
                    if rad <= 0:
                        break
                    circle(cx, cy, rad * 2, 1)
                    if rad - thick > 0:
                        circle(cx, cy, (rad - thick) * 2, 0)
                    rad -= (thick + gap)
                cross_t = val(a[7]) if len(a) > 7 else thick
                cross_l = val(a[8]) if len(a) > 8 else outer
                for ang in (0.0, 90.0):
                    p0, p1 = _rot_points([(-cross_l / 2, 0), (cross_l / 2, 0)], ang)
                    q0, q1 = place(cx + p0[0], cy + p0[1]), place(cx + p1[0], cy + p1[1])
                    mk("draw", x1=q0[0], y1=q0[1], x2=q1[0], y2=q1[1],
                       width=max(cross_t * unit_mm, 1e-4), clear=clear_default)
            elif code == 7:                                 # 热风焊盘 (近似: 环 + 4 缺口)
                cx = val(a[0]) if a else 0.0
                cy = val(a[1]) if len(a) > 1 else 0.0
                outer = val(a[2]) if len(a) > 2 else 0.0
                inner = val(a[3]) if len(a) > 3 else outer * 0.6
                gap = val(a[4]) if len(a) > 4 else outer * 0.1
                rot = val(a[5]) if len(a) > 5 else 0.0
                circle(cx, cy, outer, 1)
                circle(cx, cy, inner, 0)
                for k in range(4):
                    ang = rot + k * 90.0
                    corners = [(cx - gap / 2, cy - outer / 2), (cx + gap / 2, cy - outer / 2),
                               (cx + gap / 2, cy + outer / 2), (cx - gap / 2, cy + outer / 2)]
                    poly(_rot_points(corners, ang), 0)
        except (IndexError, ValueError, TypeError):
            continue
    return out


def _arc_sample(x1, y1, x2, y2, i, j, cw, seg: int = 24):
    """圆弧采样点 (用于精确包围盒, 避免整圆外框导致的 A/B 对齐偏差)"""
    cx, cy = x1 + i, y1 + j
    r = math.hypot(x1 - cx, y1 - cy)
    if r <= 1e-9:
        return [(x1, y1), (x2, y2)]
    a1 = math.atan2(y1 - cy, x1 - cx)
    a2 = math.atan2(y2 - cy, x2 - cx)
    if cw:
        while a2 > a1:
            a2 -= 2 * math.pi
    else:
        while a2 < a1:
            a2 += 2 * math.pi
    return [(cx + r * math.cos(a1 + (a2 - a1) * k / seg),
             cy + r * math.sin(a1 + (a2 - a1) * k / seg)) for k in range(seg + 1)]


def _arc_center_sq(x1, y1, x2, y2, i_abs, j_abs, cw):
    """G74 单象限模式: I/J 为绝对值, 需按走向选出正确的圆心符号"""
    best, best_sweep = None, None
    for si in (1, -1):
        for sj in (1, -1):
            i, j = si * i_abs, sj * j_abs
            cx, cy = x1 + i, y1 + j
            r1 = math.hypot(x1 - cx, y1 - cy)
            r2 = math.hypot(x2 - cx, y2 - cy)
            if r1 <= 1e-9 or r2 <= 1e-9:
                continue
            if abs(r1 - r2) > max(r1, r2) * 0.35:
                continue
            a1 = math.atan2(y1 - cy, x1 - cx)
            a2 = math.atan2(y2 - cy, x2 - cx)
            sweep = (a1 - a2) if cw else (a2 - a1)
            while sweep < 0:
                sweep += 2 * math.pi
            if best_sweep is None or sweep < best_sweep:
                best, best_sweep = (i, j), sweep
    return best


def _shift_prim(prim: dict, dx: float, dy: float) -> dict:
    """平移复制一个图元 (用于 %SR 拼版复制)"""
    p = dict(prim)
    for k in ("x1", "x2"):
        if p.get(k) is not None:
            p[k] = p[k] + dx
    for k in ("y1", "y2"):
        if p.get(k) is not None:
            p[k] = p[k] + dy
    if p.get("points"):
        p["points"] = [(x + dx, y + dy) for x, y in p["points"]]
    return p


def scale_fab_data(data: dict, factor: float) -> dict:
    """按统一比例缩放已解析的制造数据 (坐标/线宽/光圈/孔径), 返回新 dict

    用途:
      - 解析阶段单位推断后的整体换算;
      - 查看器中人工校正某个来源(不同 CAM 导出)的比例差异。
    """
    if not data or abs(factor - 1.0) < 1e-12:
        return data
    out = dict(data)
    prims = []
    for p in (data.get("primitives") or data.get("features") or []):
        q = dict(p)
        for k in ("x", "y", "x1", "y1", "x2", "y2", "i", "j", "width"):
            if isinstance(q.get(k), (int, float)):
                q[k] = q[k] * factor
        if q.get("points"):
            q["points"] = [(x * factor, y * factor) for x, y in q["points"]]
        prims.append(q)
    if data.get("primitives") is not None:
        out["primitives"] = prims
    if data.get("features") is not None:
        out["features"] = prims
    b = data.get("bbox")
    if b and b[0] is not None:
        out["bbox"] = [v * factor if v is not None else None for v in b]
    aps = {}
    for code, ap in (data.get("apertures") or {}).items():
        a = dict(ap)
        a["params"] = [p * factor if isinstance(p, (int, float)) else p
                       for p in (ap.get("params") or [])]
        aps[code] = a
    if data.get("apertures") is not None:
        out["apertures"] = aps
    holes = []
    for h in (data.get("holes") or []):
        hh = dict(h)
        for k in ("x", "y", "x2", "y2", "diameter_mm"):
            if isinstance(hh.get(k), (int, float)):
                hh[k] = hh[k] * factor
        holes.append(hh)
    if data.get("holes") is not None:
        out["holes"] = holes
    tools = {}
    for t, tv in (data.get("tools") or {}).items():
        d = dict(tv)
        for k in ("diameter", "diameter_mm"):
            if isinstance(d.get(k), (int, float)):
                d[k] = d[k] * factor
        tools[t] = d
    if data.get("tools") is not None:
        out["tools"] = tools
    for k in ("min_line_width_mm", "min_pad_mm", "min_hole_mm", "max_hole_mm"):
        if isinstance(data.get(k), (int, float)):
            out[k] = round(data[k] * factor, 4)
    out["unit"] = "mm"
    out["scale_applied"] = round((data.get("scale_applied") or 1.0) * factor, 6)
    return out



_FS_RE = re.compile(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", re.IGNORECASE)
_MO_RE = re.compile(r"MO(IN|MM)", re.IGNORECASE)
_SR_RE = re.compile(r"^SR(?:X(\d+)Y(\d+)I(" + _NUM + r")J(" + _NUM + r"))?", re.IGNORECASE)
# 单次扫描 G/D 命令与 X/Y/I/J 坐标, 替代逐命令多次 re.search (大幅减少大文件解析开销)
_GD_RE = re.compile(r"([GD])0*(\d+)")
_XYIJ_RE = re.compile(r"([XYIJ])(" + _NUM + r")")


def _ab_pair(cmd: str) -> Tuple[Optional[float], Optional[float]]:
    """取形如 A0.00000B0.00000 的两个数值 (%OF / %SF)"""
    nums = [_num(v) for v in re.findall(r"[-+]?\d*\.?\d+", cmd)]
    nums = [v for v in nums if v is not None]
    a = nums[0] if len(nums) > 0 else None
    b = nums[1] if len(nums) > 1 else a
    return a, b


def _infer_gerber_unit(ap_vals: List[float], span_raw: float) -> str:
    """文件未声明 %MO/G70/G71 时的单位推断

    判据:
      1) 图幅跨度是否落在正常 PCB 范围内 —— 只有一种解释成立时直接采用;
      2) 两种都成立时看光圈尺寸: 英寸文件的最小光圈通常小于 0.02 (约 2mil 以下不可能),
         毫米文件的最小光圈一般不小于 0.05, 且常有大于 0.6 的焊盘。
    推断结果写入 warnings, 查看器中还可用『比例校正』人工修正。
    """
    if span_raw and span_raw > 0:
        ok_mm = 1.0 <= span_raw <= 2000.0
        ok_in = 1.0 <= span_raw * INCH_TO_MM <= 2000.0
        if ok_in and not ok_mm:
            return "inch"
        if ok_mm and not ok_in:
            return "mm"
    vals = [v for v in ap_vals if isinstance(v, (int, float)) and v > 0]
    if vals:
        if min(vals) < 0.02:
            return "inch"
        if max(vals) > 0.6:
            return "mm"
    return "mm"


def parse_gerber(path: str, layer_name: Optional[str] = None,
                 unit_override: Optional[str] = None,
                 scale_override: Optional[float] = None) -> dict:
    """解析 RS-274X / Gerber X2

    兼容各家 CAM 导出差异 (Cadence Allegro / Genesis 2000 / InCAM / Altium / KiCad /
    CAM350 / Viewmate / GC-Prevue / 嘉立创等):
      - 扩展命令合并块: %FSLAX55Y55*MOIN*% 、%IR0*IPPOS*OFA0B0*MIA0B0*SFA1B1*%
      - 坐标格式: %FSLA/FSTA (前导零/尾随零省略)、绝对/增量 (A/I)、G90/G91
      - 单位: %MOIN/%MOMM、G70/G71, 缺失时按图幅+光圈尺寸推断 (并在 warnings 标注)
      - 图像变换: %OF 偏移、%SF 比例、%MI 镜像、%IR 旋转 (比例≠1 时图形按真实比例还原)
      - 拼版复制: %SRX..Y..I..J.. ( Genesis/InCAM 拼板常用, 否则只会显示 1 个 PCS)
      - 光圈宏: %AM 定义 + %ADD 引用 (圆/线/矩形/八角/轮廓/正多边形/靶标/热风盘)
      - 圆弧: G75 多象限 / G74 单象限 (I/J 绝对值需按走向定圆心符号)
      - 极性: %LPD/%LPC、X2 属性 %TF/%TA/%TO/%TD
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if content.startswith("\ufeff"):
        content = content[1:]

    res = GerberResult()
    res.file_name = layer_name or os.path.basename(path)

    # ---- 解析状态 ----
    unit = "mm"
    unit_declared = False
    unit_factor = 1.0                       # 文件单位 -> mm
    int_digits, dec_digits = 3, 3
    zero_mode, coord_mode = "L", "A"
    fs_seen = False
    quadrant_multi = True                   # G75 多象限 / G74 单象限
    incremental = False                     # G91 / %FS..I..
    img_scale = [1.0, 1.0]
    img_offset = [0.0, 0.0]                 # 文件单位
    img_mirror = [False, False]
    img_rot = 0
    cur_ap: Optional[int] = None
    rx = ry = 0.0                           # 当前点 (文件单位, 未变换)
    interp = 1
    cur_op = 1
    cur_negative = False
    in_region = False
    region_pts: List[Tuple[float, float]] = []
    cur_net = ""
    cur_pin = ""
    cur_refdes = ""
    macros: Dict[str, List[list]] = {}
    macro_missing: Set[str] = set()
    ap_vals: List[float] = []
    span = [None, None, None, None]         # 文件单位下的坐标范围
    sr: Optional[dict] = None               # 当前 %SR 拼版
    sr_buf: List[dict] = []

    if unit_override in ("inch", "mm"):
        unit = unit_override
        unit_declared = True
        unit_factor = INCH_TO_MM if unit == "inch" else 1.0
        res.unit = unit
        res.unit_source = "override"

    def pt(x_raw, y_raw):
        """文件单位坐标 -> mm (单位 + 比例 + 镜像 + 旋转 + 偏移)"""
        x = x_raw * unit_factor * img_scale[0]
        y = y_raw * unit_factor * img_scale[1]
        if img_mirror[0]:
            x = -x
        if img_mirror[1]:
            y = -y
        if img_rot:
            r = math.radians(img_rot)
            cs, sn = math.cos(r), math.sin(r)
            x, y = x * cs - y * sn, x * sn + y * cs
        return x + img_offset[0] * unit_factor, y + img_offset[1] * unit_factor

    def vec(dx_raw, dy_raw):
        """向量变换 (不含 %OF 偏移)"""
        x = dx_raw * unit_factor * img_scale[0]
        y = dy_raw * unit_factor * img_scale[1]
        if img_mirror[0]:
            x = -x
        if img_mirror[1]:
            y = -y
        if img_rot:
            r = math.radians(img_rot)
            cs, sn = math.cos(r), math.sin(r)
            x, y = x * cs - y * sn, x * sn + y * cs
        return x, y

    def track(x_raw, y_raw):
        if x_raw is not None:
            span[0] = x_raw if span[0] is None else min(span[0], x_raw)
            span[2] = x_raw if span[2] is None else max(span[2], x_raw)
        if y_raw is not None:
            span[1] = y_raw if span[1] is None else min(span[1], y_raw)
            span[3] = y_raw if span[3] is None else max(span[3], y_raw)

    def meta():
        return {"net": cur_net or "", "refdes": cur_refdes or "",
                "pin": cur_pin or "", "layer": res.file_name}

    def _commit(prim):
        res.primitives.append(prim)
        res.feature_count += 1
        if prim.get("x1") is not None:
            res.add_point(prim.get("x1"), prim.get("y1"))
        if prim.get("x2") is not None:
            res.add_point(prim.get("x2"), prim.get("y2"))
        for q in (prim.get("points") or []):
            res.add_point(q[0], q[1])
        if prim.get("type") == "arc":
            for q in _arc_sample(prim.get("x1") or 0.0, prim.get("y1") or 0.0,
                                 prim.get("x2") or 0.0, prim.get("y2") or 0.0,
                                 prim.get("i") or 0.0, prim.get("j") or 0.0,
                                 bool(prim.get("cw")), 12):
                res.add_point(q[0], q[1])
        net, ref, pin = prim.get("net"), prim.get("refdes"), prim.get("pin")
        if net and ref and pin:
            res.nets.setdefault(net, set()).add(f"{ref}.{pin}")

    def emit(prim, count_ap=True):
        if count_ap and prim.get("aperture"):
            code = prim["aperture"]
            res.ap_usage[code] = res.ap_usage.get(code, 0) + 1
        if sr is not None:
            sr_buf.append(prim)
            return
        _commit(prim)

    def flush_sr():
        nonlocal sr, sr_buf
        if not sr:
            return
        cnt_x = max(1, int(sr["x"]))
        cnt_y = max(1, int(sr["y"]))
        base = list(sr_buf)
        for ix in range(cnt_x):
            for iy in range(cnt_y):
                if ix == 0 and iy == 0:
                    for p in base:
                        _commit(p)
                    continue
                ddx, ddy = vec(sr["i"] * ix, sr["j"] * iy)
                for p in base:
                    _commit(_shift_prim(p, ddx, ddy))
        res.sr_steps += max(0, cnt_x * cnt_y - 1)
        sr = None
        sr_buf = []

    def ap_width(ap_code):
        """光圈等效线宽 (mm); %SF 图像比例同样作用于光圈尺寸"""
        ap = res.apertures.get(ap_code)
        if not ap:
            return None
        params = [p for p in (ap.get("params") or []) if isinstance(p, (int, float))]
        if not params:
            return None
        shape = str(ap.get("shape", "C")).upper()
        w = params[0]
        if shape in ("R", "O") and len(params) >= 2:
            w = min(params[0], params[1])
        return w * unit_factor * ((img_scale[0] + img_scale[1]) / 2.0)

    def close_region():
        nonlocal in_region, region_pts
        in_region = False
        pts = list(region_pts)
        region_pts = []
        if len(pts) < 2:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        d = meta()
        d.update({"type": "region", "points": pts,
                  "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys),
                  "width": None, "aperture": None, "clear": cur_negative})
        emit(d)

    def do_flash(x_mm, y_mm, ap_code):
        if ap_code is None:
            d = meta()
            d.update({"type": "error", "x1": x_mm, "y1": y_mm, "x2": x_mm, "y2": y_mm,
                      "error": "flash_no_aperture"})
            emit(d, count_ap=False)
            return
        ap = res.apertures.get(ap_code)
        if ap and ap.get("macro"):
            prims = macros.get(str(ap.get("shape", "")).upper())
            if prims:
                d = meta()
                d["aperture"] = ap_code
                uf = unit_factor * ((img_scale[0] + img_scale[1]) / 2.0)
                for p in _expand_macro(prims, ap.get("params") or [], uf,
                                       x_mm, y_mm, 0.0, cur_negative, d):
                    emit(p, count_ap=False)
                res.ap_usage[ap_code] = res.ap_usage.get(ap_code, 0) + 1
                return
            macro_missing.add(str(ap.get("shape", "")).upper())
        w = ap_width(ap_code)
        d = meta()
        d.update({"type": "flash", "x1": x_mm, "y1": y_mm, "x2": x_mm, "y2": y_mm,
                  "aperture": ap_code, "width": w, "clear": cur_negative})
        emit(d)

    def do_draw(x1, y1, x2, y2, ap_code):
        d = meta()
        d.update({"type": "draw", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                  "aperture": ap_code, "width": ap_width(ap_code) if ap_code else None,
                  "clear": cur_negative})
        emit(d)

    def do_arc(x1, y1, x2, y2, i_raw, j_raw, cw, ap_code):
        if quadrant_multi:
            i_mm, j_mm = vec(i_raw or 0.0, j_raw or 0.0)
        else:
            va = vec(abs(i_raw or 0.0), abs(j_raw or 0.0))
            got = _arc_center_sq(x1, y1, x2, y2, abs(va[0]), abs(va[1]), cw)
            i_mm, j_mm = got if got else vec(i_raw or 0.0, j_raw or 0.0)
        if img_mirror[0] != img_mirror[1]:
            cw = not cw                      # 单轴镜像后圆弧走向反转
        d = meta()
        d.update({"type": "arc", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                  "i": i_mm, "j": j_mm, "cw": cw, "aperture": ap_code,
                  "width": ap_width(ap_code) if ap_code else None,
                  "clear": cur_negative})
        emit(d)

    for tok in _iter_gerber_commands(content):
        kind = tok[0]

        # ---------------- 光圈宏定义 ----------------
        if kind == "am":
            macros[str(tok[1]).upper()] = _parse_macro_body(tok[2])
            res.macro_count += 1
            continue

        # ---------------- 扩展命令 (%...%) ----------------
        if kind == "ext":
            s = tok[1].strip()
            up = s.upper()
            m = _FS_RE.search(up)
            if m:
                zero_mode = (m.group(1) or "L").upper()
                coord_mode = (m.group(2) or "A").upper()
                int_digits = int(m.group(3))
                dec_digits = int(m.group(4))
                fs_seen = True
                res.zero_mode, res.coord_mode = zero_mode, coord_mode
                res.int_digits, res.dec_digits = int_digits, dec_digits
                incremental = incremental or coord_mode == "I"
                continue
            m = _MO_RE.search(up)
            if m:
                unit = "inch" if m.group(1).upper() == "IN" else "mm"
                unit_declared = True
                unit_factor = INCH_TO_MM if unit == "inch" else 1.0
                res.unit = unit
                res.unit_source = "declared(%MO)"
                continue
            if up.startswith("AD"):
                parsed = _parse_aperture(s)
                if parsed:
                    dcode, ap = parsed
                    res.apertures[dcode] = ap
                    vals = [p for p in ap["params"] if isinstance(p, (int, float))]
                    if vals:
                        ap_vals.append(vals[0])
                continue
            if up.startswith("LP"):
                cur_negative = up.startswith("LPC")
                continue
            if up.startswith("SR"):
                m = _SR_RE.match(up)
                if m and m.group(1):
                    flush_sr()
                    sr = {"x": int(m.group(1)), "y": int(m.group(2)),
                          "i": _coord_value(m.group(3), int_digits, dec_digits, zero_mode) or 0.0,
                          "j": _coord_value(m.group(4), int_digits, dec_digits, zero_mode) or 0.0}
                    sr_buf = []
                else:
                    flush_sr()
                continue
            if up.startswith("TF"):
                parts = [p.strip() for p in s.split(",")]
                head = parts[0] if parts else ""
                key = head[3:] if head.upper().startswith("TF.") else head[2:]
                val = ",".join(parts[1:]).strip()
                if key == "FileFunction":
                    res.file_function = val
                elif key == "FilePolarity":
                    res.file_polarity = val
                elif key == "Part":
                    res.file_part = val
                elif key == "GenerationSoftware":
                    res.software = val
                elif key == "CreationDate":
                    res.creation_date = val
                res.attributes_present = True
                continue
            if up.startswith("TA"):
                parts = [p.strip() for p in s.split(",")]
                if parts and parts[0].upper() == "TA.APERFUNCTION":
                    res.aperture_function = ",".join(parts[1:]).strip()
                continue
            if up.startswith("TO"):
                parts = [p.strip() for p in s.split(",")]
                head = parts[0].upper() if parts else ""
                if head == "TO.N":
                    cur_net = ",".join(parts[1:]).strip()
                    cur_refdes = cur_pin = ""
                elif head == "TO.P":
                    cur_refdes = parts[1] if len(parts) > 1 else ""
                    cur_pin = parts[2] if len(parts) > 2 else ""
                elif head == "TO.C":
                    cur_refdes = parts[1] if len(parts) > 1 else ""
                res.attributes_present = True
                continue
            if up.startswith("TD"):
                cur_net = cur_refdes = cur_pin = ""
                continue
            if up.startswith("OF"):
                a, b = _ab_pair(s[2:])
                img_offset = [a or 0.0, b if b is not None else (a or 0.0)]
                continue
            if up.startswith("SF"):
                a, b = _ab_pair(s[2:])
                img_scale = [a if a else 1.0, b if b else (a if a else 1.0)]
                continue
            if up.startswith("MI"):
                nums = re.findall(r"\d", s[2:])
                img_mirror = [(nums[0] if nums else "0") == "1",
                              (nums[1] if len(nums) > 1 else "0") == "1"]
                continue
            if up.startswith("IR"):
                v = _num(re.sub(r"[^\d.\-]", "", s[2:]))
                img_rot = int(v or 0) % 360
                continue
            if up.startswith("AS"):
                res.warnings.append("含 %AS 轴选择命令, 非常规输出, 建议重新导出")
                continue
            # %IP / %IN / %LN / %PJ / %AM 空体等: 忽略
            continue

        # ---------------- 普通功能命令 ----------------
        cmd = tok[1].strip()
        if not cmd:
            continue
        up = cmd.upper()
        if up.startswith("G04") or up.startswith("G4") or up.startswith("//"):
            continue
        if up.startswith("M"):
            continue
        # ---- G/D 命令单次扫描 (G: 插补/单位/区域; D: 光圈选择与操作码) ----
        _new_ap = _new_op = None
        for gm in _GD_RE.finditer(up):
            gi = int(gm.group(2))
            if gm.group(1) == "G":
                if gi in (0, 1):
                    interp = 1
                elif gi == 2:
                    interp = 2
                elif gi == 3:
                    interp = 3
                elif gi == 36:
                    in_region = True
                    region_pts = []
                elif gi == 37:
                    close_region()
                elif gi == 70:
                    unit, unit_declared = "inch", True
                    unit_factor = INCH_TO_MM
                    res.unit, res.unit_source = unit, "declared(G70)"
                elif gi == 71:
                    unit, unit_declared = "mm", True
                    unit_factor = 1.0
                    res.unit, res.unit_source = unit, "declared(G71)"
                elif gi == 74:
                    quadrant_multi = False
                elif gi == 75:
                    quadrant_multi = True
                elif gi == 90:
                    incremental = False
                elif gi == 91:
                    incremental = True
            else:                       # D: 光圈选择 (nn>=10) / 操作码 (01/02/03, 模态)
                if gi >= 10:
                    _new_ap = gi
                elif gi in (1, 2, 3):
                    _new_op = gi
        if _new_ap is not None:
            cur_ap = _new_ap
        if _new_op is not None:
            cur_op = _new_op

        # ---- 坐标 X/Y/I/J 单次扫描 (省略 = 沿用当前值) ----
        mx = my = mi = mj = None
        for cm in _XYIJ_RE.finditer(cmd):
            k = cm.group(1)
            if k == "X":
                if mx is None:
                    mx = cm.group(2)
            elif k == "Y":
                if my is None:
                    my = cm.group(2)
            elif k == "I":
                if mi is None:
                    mi = cm.group(2)
            elif mj is None:
                mj = cm.group(2)
        if mx is None and my is None and mi is None and mj is None:
            continue
        x_raw = _coord_value(mx, int_digits, dec_digits, zero_mode) if mx is not None else None
        y_raw = _coord_value(my, int_digits, dec_digits, zero_mode) if my is not None else None
        i_raw = _coord_value(mi, int_digits, dec_digits, zero_mode) if mi is not None else None
        j_raw = _coord_value(mj, int_digits, dec_digits, zero_mode) if mj is not None else None
        if incremental:
            if x_raw is not None:
                x_raw += rx
            if y_raw is not None:
                y_raw += ry
        nx_raw = x_raw if x_raw is not None else rx
        ny_raw = y_raw if y_raw is not None else ry
        track(nx_raw, ny_raw)
        nx, ny = pt(nx_raw, ny_raw)
        cx_mm, cy_mm = pt(rx, ry)
        op = cur_op

        if in_region:
            if op in (1, 2):
                region_pts.append((nx, ny))
            rx, ry = nx_raw, ny_raw
            continue
        if op == 3:
            do_flash(nx, ny, cur_ap)
        elif op == 1:
            if interp in (2, 3):
                do_arc(cx_mm, cy_mm, nx, ny, i_raw, j_raw, interp == 2, cur_ap)
            else:
                do_draw(cx_mm, cy_mm, nx, ny, cur_ap)
        rx, ry = nx_raw, ny_raw

    flush_sr()
    if in_region:
        close_region()
    if not fs_seen:
        res.warnings.append("未找到 %FS 坐标格式声明, 已按 3.3 解析")
    if fs_seen and span[0] is not None:
        max_abs = max([abs(v) for v in span if v is not None] or [0.0])
        if max_abs >= 10 ** int_digits:
            res.warnings.append(
                f"坐标超出 %FS 声明的整数位({int_digits}位)范围, 格式声明可能与数据不符")
    if zero_mode == "T":
        res.warnings.append("坐标为尾随零省略(FSTA)")
    if img_scale != [1.0, 1.0]:
        res.warnings.append("含图像比例 %%SF=%.4f/%.4f, 已按比例还原" % tuple(img_scale))
    if any(img_mirror):
        res.warnings.append("含图像镜像 %%MI=%s" % ("A" if img_mirror[0] else "") +
                            ("B" if img_mirror[1] else ""))
    if img_rot:
        res.warnings.append(f"含图像旋转 %IR{img_rot}")
    if res.sr_steps:
        res.warnings.append(f"含拼版复制 %SR (共 {res.sr_steps + 1} 份)")
    if macro_missing:
        res.warnings.append("引用了未定义的光圈宏: " + ",".join(sorted(macro_missing)))
    if res.file_polarity.upper().startswith("NEG"):
        res.warnings.append("文件为负极性(FilePolarity=Negative)")

    res.unit = unit
    data = {
        "kind": "gerber",
        "file": path,
        "name": res.file_name,
        "unit": res.unit,
        "unit_source": res.unit_source,
        "format": f"{res.int_digits}.{res.dec_digits}",
        "zero_mode": res.zero_mode,
        "coord_mode": res.coord_mode,
        "software": res.software,
        "creation_date": res.creation_date,
        "file_polarity": res.file_polarity,
        "image_scale": list(img_scale),
        "image_offset": list(img_offset),
        "image_mirror": list(img_mirror),
        "image_rotation": img_rot,
        "sr_steps": res.sr_steps,
        "macro_count": res.macro_count,
        "apertures": res.apertures,
        "primitives": res.primitives,
        "bbox": res.bbox,
        "feature_count": res.feature_count,
        "attributes_present": res.attributes_present,
        "nets": {k: sorted(v) for k, v in res.nets.items()},
        "min_line_width_mm": _min_line_width(res),
        "min_pad_mm": _min_pad(res),
        "aperture_count": len(res.apertures),
        "file_function": res.file_function,
        "file_part": res.file_part,
        "aperture_function": res.aperture_function,
        "warnings": list(res.warnings),
    }

    # ---- 单位未声明: 按图幅/光圈推断, 并整体换算到 mm ----
    if not unit_declared:
        span_raw = 0.0
        if span[0] is not None and span[2] is not None:
            span_raw = max(span[2] - span[0],
                           (span[3] - span[1]) if span[1] is not None else 0.0)
        guess = _infer_gerber_unit(ap_vals, span_raw)
        data["unit_source"] = f"inferred({guess})"
        data["warnings"].append(
            f"文件未声明单位(%MO/G70/G71), 已按{'英寸' if guess == 'inch' else '毫米'}推断")
        if guess == "inch":
            data = scale_fab_data(data, INCH_TO_MM)
            data["unit_source"] = "inferred(inch)"
            data["warnings"] = data.get("warnings") or []
        data["warnings"].append("如与其它来源图层比例不符, 请用『比例校正』手动指定")

    if scale_override and abs(scale_override - 1.0) > 1e-9:
        data = scale_fab_data(data, scale_override)
        data.setdefault("warnings", []).append(f"已应用人工比例校正 ×{scale_override:g}")

    b = data.get("bbox")
    if b and b[0] is not None:
        w, h = b[2] - b[0], b[3] - b[1]
        if max(w, h) > 1200:
            data.setdefault("warnings", []).append(
                f"图幅 {w:.0f}×{h:.0f}mm 异常偏大, 请确认单位/比例声明 (可能为图纸层或拼版)")
        elif max(w, h) < 1:
            data.setdefault("warnings", []).append(
                f"图幅仅 {w:.2f}×{h:.2f}mm 异常偏小, 请确认坐标格式与单位声明")
    return data


def _min_line_width(res: GerberResult) -> Optional[float]:
    widths = [p.get("width") for p in res.primitives
              if p.get("type") in ("draw", "arc") and p.get("width")]
    return round(min(widths), 4) if widths else None


def _min_pad(res: GerberResult) -> Optional[float]:
    sizes = []
    for dcode, ap in res.apertures.items():
        params = ap.get("params") or []
        if params and isinstance(params[0], (int, float)):
            size = params[0]
            if ap.get("shape") in ("R", "O") and len(params) >= 2 and isinstance(params[1], (int, float)):
                size = min(params[0], params[1])
            sizes.append(size * (INCH_TO_MM if res.unit == "inch" else 1.0))
    return round(min(sizes), 4) if sizes else None


# ============================================================
# Excellon NC Drill
# ============================================================
_DRILL_XY_RE = re.compile(r"^(?:G0*[0-9]\s*)?X([-+]?\d*\.?\d+)?\s*Y([-+]?\d*\.?\d+)?(.*)$",
                          re.IGNORECASE)
_DRILL_TOOL_RE = re.compile(r"^T(\d+)\s*C\s*([-+]?\d*\.?\d+)\s*(MM|MIL|MILS|INCH)?", re.IGNORECASE)


def _drill_value(raw: Optional[str], unit: str, zero_mode: str = "L",
                 dec_digits: int = 4, total_digits: int = 6) -> Optional[float]:
    """Excellon 坐标 -> mm (含单位换算; 旧实现漏掉英寸换算导致钻孔与线路差 25.4 倍)"""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    if "." in raw:
        v = _num(raw)
        if v is None:
            return None
    else:
        neg = raw.startswith("-")
        digits = raw.lstrip("+-")
        if not digits.isdigit():
            return None
        if zero_mode == "T":
            digits = digits.ljust(total_digits, "0")[:total_digits]
        v = int(digits) / (10 ** dec_digits) if dec_digits else float(int(digits))
        if neg:
            v = -v
    return v * (INCH_TO_MM if unit == "inch" else 1.0)


def _excellon_span_ok(unit: str, span_raw: float, dec: int, hole_count: int = 0) -> bool:
    """该小数位解读是否物理可行: 图幅在 3 ~ 1500 mm 之间, 且孔密度 <= 4 孔/mm²"""
    if not span_raw or dec < 0:
        return False
    span = span_raw / (10 ** dec) * (INCH_TO_MM if unit == "inch" else 1.0)
    if not (3.0 <= span <= 1500.0):
        return False
    if hole_count > 0 and hole_count / max(span * span * 0.5, 1e-6) > 4.0:
        return False
    return True


def _infer_excellon_dec(unit: str, span_raw: float, hole_count: int = 0,
                        trailing_zero_ratio: float = 0.0) -> int:
    """无显式小数点时推断 Excellon 坐标小数位

    各家输出差异很大: Allegro 有固定 10 位(5.5 英寸)/2.4/2.5 英寸, Genesis/InCAM/KiCad
    多为 3.3 毫米。小数点位置无法从数字本身唯一确定, 因此:
      1) 图幅必须落在 [REDACTED-UNKNOWN]00mm, 且孔密度物理可行 (见 _excellon_span_ok);
      2) 余下候选取「最接近 100mm 典型板件尺寸」者;
      3) 坐标几乎都以 0 结尾时, 说明真实分辨率更粗, 倾向更少的小数位。
    仍不确定时会在 warnings 中提示, 查看器还会用线路层包围盒做交叉校验。
    """
    cands = (3, 4, 5, 6, 7) if unit == "inch" else (2, 3, 4, 5, 6)
    best, best_score = None, None
    for dec in cands:
        if not _excellon_span_ok(unit, span_raw, dec, hole_count):
            continue
        span = span_raw / (10 ** dec) * (INCH_TO_MM if unit == "inch" else 1.0)
        score = abs(math.log10(span / 100.0))
        if trailing_zero_ratio >= 0.9:
            score += 0.25 * (dec - cands[0])
        if best_score is None or score < best_score:
            best, best_score = dec, score
    if best is not None:
        return best
    return 4 if unit == "inch" else 3


def parse_excellon(path: str, plating: Optional[str] = None,
                   unit_override: Optional[str] = None,
                   format_override: Optional[Tuple[int, int]] = None,
                   scale_override: Optional[float] = None) -> dict:
    """解析 Excellon NC 钻孔

    兼容 Allegro / Genesis 2000 / InCAM / Altium / KiCad / Eagle / CAM350 / 嘉立创 等输出:
      - 单位: METRIC[,LZ|,TZ] / INCH[,LZ|,TZ] / M71 / M72; 缺失时按刀具注释(MILS/MM)与图幅推断
      - 坐标: 显式小数点、前导零省略(LZ)、尾随零省略(TZ)、无小数点时自动推断 2.4/2.5/3.3 等格式
      - 刀具: T##C## 定义, 以及 Allegro 只在注释里给孔径的写法 (;T02 Holesize 1. = 10.0 ... MILS)
      - 槽孔: G85 / M15+G01 路由; 重复: R##; 模态坐标: 省略 X 或 Y 时沿用上一值
      - 孔性质: PLATED / NON_PLATED (按刀具), 或文件名 PTH/NPTH
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    name = os.path.basename(path).upper()
    if plating is None:
        if "NPTH" in name or "NON" in name:
            plating = "NPTH"
        elif "PTH" in name or "DRL" in name:
            plating = "PTH"
        else:
            plating = "unknown"

    tools: Dict[int, dict] = {}
    tool_plating: Dict[int, str] = {}
    warnings: List[str] = []
    unit = unit_override if unit_override in ("inch", "mm") else None
    unit_source = "override" if unit else ""
    zero_declared = ""
    dec_digits = format_override[1] if format_override else None
    int_digits = format_override[0] if format_override else None
    hint_unit = None

    # ---------- 第 1 遍: 单位/刀具/坐标格式统计 ----------
    max_digits = 0
    has_decimal = False
    coord_count = 0
    trailing_zero = 0
    token_count = 0
    lead_zero_tokens = 0
    len_hist: Dict[int, int] = {}
    ordered_sizes: List[Tuple[Optional[int], Optional[float], Optional[str]]] = []
    span = [None, None, None, None]
    units_seen: List[str] = []

    def _track(v):
        if v is None:
            return
        span[0] = v if span[0] is None else min(span[0], v)
        span[2] = v if span[2] is None else max(span[2], v)

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if line.startswith((";", "#", "//")):
            # Allegro 常把孔径只写在注释里:
            #   ;T02 Holesize 1. = 10.000000 ... PLATED MILS Quantity = 830
            #   ;   Holesize 1. = 8.000000 ... PLATED MILS Quantity = 605   (无 T 号, 按顺序对应 T1/T2..)
            m = re.search(r"(?:T(\d+)\s+)?HOLESIZE\s*\d*\s*\.?\s*=\s*([0-9.]+)", up)
            if m:
                dia = _num(m.group(2))
                tnum = int(m.group(1)) if m.group(1) else None
                dia_mm = None
                if dia is not None:
                    if "MIL" in up:
                        dia_mm = dia * MIL_TO_MM
                        hint_unit = hint_unit or "inch"
                    elif "MICRON" in up:
                        dia_mm = dia * 0.001
                        hint_unit = hint_unit or "mm"
                    elif re.search(r"\bMM\b", up):
                        dia_mm = dia
                        hint_unit = hint_unit or "mm"
                plated = None
                if "NON_PLATED" in up or "NON-PLATED" in up:
                    plated = "NPTH"
                elif "PLATED" in up:
                    plated = "PTH"
                ordered_sizes.append((tnum, dia_mm, plated))
                if tnum is not None and dia_mm:
                    t = tools.setdefault(tnum, {})
                    t["diameter_mm"] = round(dia_mm, 6)
                    t["source"] = "comment"
                if tnum is not None and plated:
                    tool_plating[tnum] = plated
            if "FORMAT" in up:
                m = re.search(r"FORMAT\s*=?\s*(\d)\s*[:.]?\s*(\d)", up)
                if m and dec_digits is None:
                    int_digits, dec_digits = int(m.group(1)), int(m.group(2))
            continue
        if up.startswith("METRIC") or up == "M71":
            units_seen.append("mm")
            if "," in up:
                zero_declared = "T" if "TZ" in up else ("L" if "LZ" in up else zero_declared)
            continue
        if up.startswith("INCH") or up == "M72":
            units_seen.append("inch")
            if "," in up:
                zero_declared = "T" if "TZ" in up else ("L" if "LZ" in up else zero_declared)
            continue
        m = _DRILL_TOOL_RE.match(line)
        if m:
            d = _num(m.group(2))
            tnum = int(m.group(1))
            suffix = (m.group(3) or "").upper()
            if suffix.startswith("MIL"):
                dia_mm = d * MIL_TO_MM if d is not None else None
                hint_unit = hint_unit or "inch"
            elif suffix == "MM":
                dia_mm = d
                hint_unit = hint_unit or "mm"
            else:
                dia_mm = None
            tools.setdefault(tnum, {})
            tools[tnum]["diameter"] = d
            if dia_mm is not None:
                tools[tnum]["diameter_mm"] = round(dia_mm, 6)
            continue
        m = _DRILL_XY_RE.match(line)
        if m and (m.group(1) or m.group(2)):
            coord_count += 1
            for g in (m.group(1), m.group(2)):
                if not g:
                    continue
                if "." in g:
                    has_decimal = True
                    continue
                digits = g.lstrip("+-")
                if not digits.isdigit():
                    continue
                token_count += 1
                max_digits = max(max_digits, len(digits))
                len_hist[len(digits)] = len_hist.get(len(digits), 0) + 1
                if digits.startswith("0"):
                    lead_zero_tokens += 1
                if digits.endswith("0"):
                    trailing_zero += 1
                _track(int(g))
            continue

    span_raw = max([abs(v) for v in span if v is not None] or [0.0])
    tz_ratio = (trailing_zero / max(1, token_count)) if token_count else 0.0
    lead_ratio = (lead_zero_tokens / max(1, token_count)) if token_count else 0.0
    fixed_width = bool(len_hist) and len(len_hist) == 1

    # ---- 零省略方式: 以实际数据为准 (各家对 ,LZ/,TZ 的含义并不统一) ----
    #   定宽字段 (所有坐标位数相同) => 前导零补齐, 右对齐解读即可 (补零为空操作);
    #   变长且多以 0 开头 => 前导零保留、尾随零省略 => 需右补零到最大宽度;
    #   变长且多不以 0 开头 => 前导零省略 => 直接按小数位还原。
    zero_mode = "L" if fixed_width else ("T" if lead_ratio >= 0.5 else "L")
    if zero_declared and zero_declared != zero_mode:
        warnings.append(f"文件声明 {zero_declared}Z, 但坐标特征更像 "
                        f"{'尾随零省略' if zero_mode == 'T' else '前导零省略'}, 已按实际数据解析")

    # ---- 单位判定 ----
    if unit is None:
        if len(set(units_seen)) == 1:
            unit = units_seen[0]
            unit_source = "declared"
        elif len(set(units_seen)) > 1:
            unit = units_seen[-1]
            unit_source = "declared"
            warnings.append("文件中出现多种单位声明, 按最后声明解析")
        elif hint_unit:
            unit = hint_unit
            unit_source = "inferred(刀具注释)"
            warnings.append(f"未声明单位, 已按刀具注释推断为{'英寸' if unit == 'inch' else '毫米'}")
        else:
            unit = "inch"
            unit_source = "default(inch)"
            warnings.append("未声明单位, 已按 Excellon 默认英寸解析")

    # ---- 小数位判定 ----
    if dec_digits is None:
        if has_decimal:
            dec_digits = 0
            warnings.append("坐标带小数点, 直接按数值解析")
        else:
            std = None
            if fixed_width and max_digits:
                # 固定字段宽度 (前导零保留): 按各家标准格式取小数位
                #   英寸: 2.4(6位) / 2.5(7位) / 3.5(8位) / 5.5(10位) -> 小数位 4 或 5
                #   毫米: 3.3(6位) / 4.3(7位) -> 小数位 3
                if unit == "inch":
                    std = (max_digits - 2) if max_digits <= 6 else 5
                else:
                    std = 3 if max_digits <= 7 else max(3, max_digits - 4)
            if std is not None and _excellon_span_ok(unit, span_raw, std, coord_count):
                dec_digits = std
            else:
                dec_digits = _infer_excellon_dec(unit, span_raw, coord_count, tz_ratio)
            span_mm = span_raw / (10 ** dec_digits) * (INCH_TO_MM if unit == "inch" else 1.0)
            warnings.append(
                f"坐标无小数点, 已推断 {dec_digits} 位小数"
                f"({'尾随零省略' if zero_mode == 'T' else '前导零省略'}), 图幅约 {span_mm:.1f}mm")
    if int_digits is None:
        if zero_mode == "T" and max_digits:
            int_digits = max(1, max_digits - dec_digits)
        else:
            int_digits = 2 if unit == "inch" else 3      # 前导零省略: 整数位仅作展示
    total_digits = max(int_digits + dec_digits, max_digits)

    # 孔径: 注释按顺序给出但未带 T 号时, 依次对应 T1/T2/...
    auto_t = 0
    for tnum, dia_mm, plated in ordered_sizes:
        if tnum is None:
            auto_t += 1
            while auto_t in tools and tools[auto_t].get("diameter_mm"):
                auto_t += 1
            t = tools.setdefault(auto_t, {})
            if dia_mm:
                t["diameter_mm"] = round(dia_mm, 6)
                t["source"] = "comment"
            if plated:
                tool_plating.setdefault(auto_t, plated)

    # 刀具孔径: 无显式定义时按文件单位换算
    for tnum, t in tools.items():
        if t.get("diameter_mm") is None and t.get("diameter") is not None:
            t["diameter_mm"] = round(
                t["diameter"] * (INCH_TO_MM if unit == "inch" else 1.0), 6)

    # ---------- 第 2 遍: 生成孔位 ----------
    holes: List[dict] = []
    cur_tool: Optional[int] = None
    cur_unit = unit
    cur_x = cur_y = None
    last_dx = last_dy = 0.0
    rout_on = False
    prev_point: Optional[Tuple[float, float]] = None

    def conv(raw):
        return _drill_value(raw, cur_unit, zero_mode, dec_digits, total_digits)

    def add_hole(x, y, slot_to=None):
        if x is None or y is None:
            return
        t = tools.get(cur_tool, {}) if cur_tool is not None else {}
        h = {
            "x": x, "y": y, "tool": cur_tool,
            "diameter_mm": t.get("diameter_mm"),
            "plating": tool_plating.get(cur_tool, plating) if cur_tool is not None else plating,
            "is_slot": bool(slot_to),
        }
        if slot_to:
            h["x2"], h["y2"] = slot_to
        holes.append(h)

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        up = line.upper()
        if up in ("M48", "%", "M95", "M30", "M31", "M00", "G90", "G91", "G05", "FMAT,2"):
            continue
        if up.startswith("FMAT") or up.startswith("ICL") or up.startswith("VER") \
                or up.startswith("DET") or up.startswith("REMARK"):
            continue
        if up.startswith("METRIC") or up == "M71":
            cur_unit = "mm"
            continue
        if up.startswith("INCH") or up == "M72":
            cur_unit = "inch"
            continue
        if up.startswith("M15"):
            rout_on = True
            continue
        if up.startswith("M16") or up.startswith("M17"):
            rout_on = False
            continue
        m = _DRILL_TOOL_RE.match(line)
        if m:
            cur_tool = int(m.group(1))
            continue
        m = re.match(r"^T(\d+)\s*$", line)
        if m:
            cur_tool = int(m.group(1))
            continue
        if up.startswith("G85"):
            # 槽孔终点
            m = re.search(r"X([-+]?\d*\.?\d+)?\s*Y([-+]?\d*\.?\d+)?", line, re.IGNORECASE)
            if m and holes:
                ex = conv(m.group(1)) if m.group(1) else None
                ey = conv(m.group(2)) if m.group(2) else None
                last = holes[-1]
                if ex is not None or ey is not None:
                    last["is_slot"] = True
                    last["x2"] = ex if ex is not None else last["x"]
                    last["y2"] = ey if ey is not None else last["y"]
            continue
        m = _DRILL_XY_RE.match(line)
        if not m or not (m.group(1) or m.group(2)):
            continue
        if cur_tool is None:
            cur_tool = 1
        x = conv(m.group(1))
        y = conv(m.group(2))
        nx = x if x is not None else cur_x
        ny = y if y is not None else cur_y
        tail = (m.group(3) or "").upper()
        if rout_on and prev_point is not None and (nx is not None and ny is not None):
            add_hole(prev_point[0], prev_point[1], slot_to=(nx, ny))
        elif nx is not None and ny is not None:
            add_hole(nx, ny)
        rep = re.search(r"R0*(\d+)", tail)
        if rep and nx is not None and ny is not None:
            cnt = max(0, int(rep.group(1)) - 1)
            for _ in range(min(cnt, 10000)):
                nx += last_dx
                ny += last_dy
                add_hole(nx, ny)
        if x is not None and cur_x is not None:
            last_dx = x
        if y is not None and cur_y is not None:
            last_dy = y
        if x is not None and y is not None:
            last_dx, last_dy = 0.0, 0.0
            if cur_x is not None and cur_y is not None:
                last_dx, last_dy = x - cur_x, y - cur_y
        prev_point = (nx, ny) if (nx is not None and ny is not None) else prev_point
        cur_x = nx if nx is not None else cur_x
        cur_y = ny if ny is not None else cur_y

    bbox = [None, None, None, None]
    for h in holes:
        for kx, ky in (("x", "y"), ("x2", "y2")):
            if h.get(kx) is None or h.get(ky) is None:
                continue
            bbox[0] = h[kx] if bbox[0] is None else min(bbox[0], h[kx])
            bbox[1] = h[ky] if bbox[1] is None else min(bbox[1], h[ky])
            bbox[2] = h[kx] if bbox[2] is None else max(bbox[2], h[kx])
            bbox[3] = h[ky] if bbox[3] is None else max(bbox[3], h[ky])

    if not holes:
        warnings.append("未解析到任何孔位, 请确认文件格式")
    if scale_override and abs(scale_override - 1.0) > 1e-9:
        data0 = {"kind": "drill", "holes": holes, "bbox": bbox, "tools": tools}
        scaled = scale_fab_data(data0, scale_override)
        holes = scaled["holes"]
        bbox = scaled["bbox"]
        tools = scaled["tools"]
        warnings.append(f"已应用人工比例校正 ×{scale_override:g}")

    diameters = [h["diameter_mm"] for h in holes if h.get("diameter_mm")]
    return {
        "kind": "drill",
        "file": path,
        "name": os.path.basename(path),
        "unit": "mm",
        "source_unit": cur_unit,
        "unit_source": unit_source,
        "format": f"{int_digits}.{dec_digits}",
        "zero_mode": zero_mode,
        "plating": plating,
        "tools": tools,
        "holes": holes,
        "hole_count": len(holes),
        "slot_count": sum(1 for h in holes if h.get("is_slot")),
        "min_hole_mm": round(min(diameters), 4) if diameters else None,
        "max_hole_mm": round(max(diameters), 4) if diameters else None,
        "bbox": bbox,
        "warnings": warnings,
    }


def _parse_drill_coord(raw: str, unit: str) -> Optional[float]:
    """兼容旧接口: 按 2.4(英寸)/3.3(毫米) 解析并换算为 mm"""
    if unit == "inch":
        return _drill_value(raw, "inch", "L", 4, 6)
    return _drill_value(raw, "mm", "L", 3, 6)


# ============================================================
# ODB++
# ============================================================
def parse_odbpp(job_dir: str) -> dict:
    matrix_path = os.path.join(job_dir, "matrix", "matrix")
    layers: List[dict] = []
    features: List[dict] = []
    nets: Dict[str, Set[str]] = {}
    units = "inch"

    if os.path.exists(matrix_path):
        with open(matrix_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("UNITS"):
                    val = line.split("=")[-1].strip().upper()
                    units = "mm" if val.startswith("MM") else "inch"
                m = re.match(r"^LAYER\s*\{?\s*(\S+)\s+(\S+)", line)
                if m:
                    layers.append({"name": m.group(1), "type": m.group(2)})

    scale = INCH_TO_MM if units == "inch" else 1.0
    layers_dir = os.path.join(job_dir, "layers")
    if os.path.isdir(layers_dir):
        for lname in os.listdir(layers_dir):
            feat_path = os.path.join(layers_dir, lname, "features")
            if not os.path.isfile(feat_path):
                continue
            symbols: Dict[str, dict] = {}
            with open(feat_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    if line.startswith("$"):
                        parts = line[1:].split()
                        if len(parts) >= 2:
                            symbols[parts[0]] = {"type": parts[1], "params": parts[2:]}
                        continue
                    if line.startswith("#"):
                        continue
                    parts = line.split()
                    if not parts:
                        continue
                    rec = parts[0]
                    attrs = {}
                    if ";" in line:
                        body, attr_part = line.split(";", 1)
                        parts = body.split()
                        for a in attr_part.split(";"):
                            if "=" in a:
                                k, v = a.split("=", 1)
                                attrs[k] = v
                    def g(i):
                        try:
                            return float(parts[i]) * scale
                        except (IndexError, ValueError):
                            return None
                    if rec == "L" and len(parts) >= 6:
                        features.append({"type": "draw", "x1": g(1), "y1": g(2),
                                         "x2": g(3), "y2": g(4), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})
                    elif rec == "P" and len(parts) >= 3:
                        features.append({"type": "flash", "x1": g(1), "y1": g(2),
                                         "x2": g(1), "y2": g(2), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})
                    elif rec == "A" and len(parts) >= 7:
                        features.append({"type": "arc", "x1": g(1), "y1": g(2),
                                         "x2": g(5), "y2": g(6), "layer": lname,
                                         "net": attrs.get("net", ""), "refdes": attrs.get("c", ""),
                                         "pin": attrs.get("pn", "")})

    for net_file in (os.path.join(job_dir, "netlists", "cadnet", "netlist"),
                     os.path.join(job_dir, "netlists", "netlist")):
        if os.path.isfile(net_file):
            nets.update(_parse_odb_netlist(net_file))
            break

    bbox = [None, None, None, None]
    for feat in features:
        for (x, y) in ((feat.get("x1"), feat.get("y1")), (feat.get("x2"), feat.get("y2"))):
            if x is None or y is None:
                continue
            bbox[0] = x if bbox[0] is None else min(bbox[0], x)
            bbox[1] = y if bbox[1] is None else min(bbox[1], y)
            bbox[2] = x if bbox[2] is None else max(bbox[2], x)
            bbox[3] = y if bbox[3] is None else max(bbox[3], y)

    return {
        "kind": "odbpp",
        "file": job_dir,
        "name": os.path.basename(os.path.normpath(job_dir)),
        "unit": units,
        "layers": layers,
        "layer_count": len(layers),
        "features": features,
        "feature_count": len(features),
        "nets": {k: sorted(v) for k, v in nets.items()},
        "bbox": bbox,
        "attributes_present": bool(nets),
    }


def _parse_odb_netlist(path: str) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    cur = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if ";" in line and not line.startswith("+"):
                head, *nodes = line.split(";")
                head = head.strip()
                cur = head
                nets.setdefault(cur, set())
                for n in nodes:
                    n = n.strip()
                    if n:
                        nets[cur].add(n.replace("-", "."))
                continue
            m = re.match(r"^(\d+)\s+(\S+)", line)
            if m:
                cur = m.group(2)
                nets.setdefault(cur, set())
                continue
            if line.startswith("+"):
                node = line[1:].strip().replace("-", ".")
                if cur and node:
                    nets[cur].add(node)
                continue
            if cur:
                nets[cur].add(line.replace("-", "."))
    return nets


# ============================================================
# 统一读取
# ============================================================
def read_fab_data(path) -> List[dict]:
    """读取 Gerber/Excellon/ODB++ (目录/文件/路径列表), 返回数据列表"""
    if isinstance(path, (list, tuple)):
        result: List[dict] = []
        for p in path:
            result.extend(read_fab_data(p))
        return result
    result: List[dict] = []
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, "matrix")) or \
           os.path.isfile(os.path.join(path, "matrix", "matrix")):
            result.append(parse_odbpp(path))
        else:
            for root, _dirs, files in os.walk(path):
                for fn in sorted(files):
                    result.extend(_read_single(os.path.join(root, fn)))
    else:
        result.extend(_read_single(path))
    return result


def _read_single(path: str) -> List[dict]:
    low = path.lower()
    if low.endswith(".drl") or "drill" in low or "npt" in low:
        try:
            return [parse_excellon(path)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析钻孔文件失败, 已跳过: %s (%s)", path, exc)
            return []
    if low.endswith((".gbr", ".ger", ".art", ".pho")) or _looks_like_gerber(path):
        try:
            return [parse_gerber(path)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析 Gerber 文件失败, 已跳过: %s (%s)", path, exc)
            return []
    return []


def _looks_like_gerber(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
        return "%FS" in head or "%MO" in head or "%ADD" in head or "G04" in head
    except OSError as exc:
        logger.debug("探测 Gerber 文件失败: %s (%s)", path, exc)
        return False


# ============================================================
# 完整性检查
# ============================================================
def integrity_check(fab_data: List[dict]) -> List[dict]:
    issues: List[dict] = []
    code_counter = [0]

    def add(category, rule, severity, description, x=None, y=None,
            measured=None, required=None, unit="", suggestion="", layer=""):
        code_counter[0] += 1
        issues.append(make_issue(
            code=f"{INTEGRITY_ISSUE_PREFIX}-{code_counter[0]:02d}",
            category=category, rule=rule, severity=severity,
            description=description, x=x, y=y, measured=measured,
            required=required, unit=unit, suggestion=suggestion, layer=layer,
        ))

    if not fab_data:
        add("制造数据", "数据缺失", "危险", "未解析到任何 Gerber/ODB++/NC Drill 制造数据",
            suggestion="确认制造文件路径与格式")
        return issues

    gerbers = [d for d in fab_data if d["kind"] == "gerber"]
    drills = [d for d in fab_data if d["kind"] == "drill"]
    odb = [d for d in fab_data if d["kind"] == "odbpp"]

    if not gerbers and not odb:
        add("制造数据", "图形层缺失", "危险", "缺少线路/图形层 (Gerber 或 ODB++)",
            suggestion="补充 Gerber 或 ODB++ 数据")
    if not drills and not odb:
        add("制造数据", "钻孔层缺失", "危险", "缺少 NC Drill / ODB++ 钻孔数据",
            suggestion="补充 NC Drill 文件")

    seen_layers: Set[str] = set()
    for g in gerbers:
        name = g["name"].lower()
        base = re.sub(r"[^a-z]", "", name)
        if base in seen_layers:
            add("制造数据", "图层重复", "中",
                f"检测到疑似重复图层: {g['name']}", layer=g["name"],
                suggestion="核对叠层/图层命名, 删除重复输出")
        seen_layers.add(base)
        if not g["attributes_present"]:
            add("制造数据", "网络属性缺失", "中",
                f"图层 {g['name']} 无 X2 网络属性 (TO.N/TO.P), 无法进行网络表比对",
                layer=g["name"], suggestion="EDA 导出 Gerber 时启用 X2 属性")
        if g["feature_count"] == 0:
            add("制造数据", "空图层", "高",
                f"图层 {g['name']} 无有效图形", layer=g["name"],
                suggestion="检查该层是否漏输出")
        if g["bbox"][0] is not None and g["bbox"][2] is not None:
            w = g["bbox"][2] - g["bbox"][0]
            h = g["bbox"][3] - g["bbox"][1]
            if w <= 0 or h <= 0:
                add("制造数据", "坐标偏移", "高",
                    f"图层 {g['name']} 图形范围异常 (W={w:.3f}, H={h:.3f})", layer=g["name"],
                    suggestion="检查坐标系/偏移与单位设定")
        for prim in g["primitives"]:
            if prim.get("type") == "error":
                add("制造数据", "孔径未定义", "高",
                    f"图层 {g['name']} 存在未定义光圈的 Flash", layer=g["name"],
                    x=prim.get("x1"), y=prim.get("y1"),
                    suggestion="修正光圈定义 (ADD)")
                break
        for dcode, ap in (g.get("apertures") or {}).items():
            params = ap.get("params") or []
            if params and isinstance(params[0], (int, float)) and params[0] <= 0:
                add("制造数据", "孔径定义错误", "高",
                    f"图层 {g['name']} 光圈 D{dcode} 尺寸<=0", layer=g["name"],
                    suggestion="修正光圈尺寸定义")

    for d in drills:
        if d.get("hole_count", 0) == 0:
            add("制造数据", "钻孔为空", "高", f"钻孔文件 {d['name']} 无孔", layer=d["name"],
                suggestion="检查钻孔输出")
        if d.get("plating") == "unknown":
            add("制造数据", "镀通孔标注", "低",
                f"钻孔文件 {d['name']} 未标注 PTH/NPTH", layer=d["name"],
                suggestion="按镀通孔/非镀通孔分文件输出")

    if len(gerbers) > 1:
        boxes = [(g["name"], g["bbox"]) for g in gerbers if g["bbox"][0] is not None]
        if boxes:
            ref = boxes[0][1]
            for name, b in boxes[1:]:
                if abs(b[0] - ref[0]) > 1.0 or abs(b[1] - ref[1]) > 1.0:
                    add("制造数据", "层间坐标偏移", "高",
                        f"图层 {name} 与 {boxes[0][0]} 坐标原点偏移 >1mm", layer=name,
                        x=b[0], y=b[1],
                        suggestion="统一各层输出坐标系/原点")

    return issues


# ============================================================
# 网络表比对
# ============================================================
def collect_fab_nets(fab_data: List[dict]) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    for d in fab_data:
        for net, nodes in (d.get("nets") or {}).items():
            nets.setdefault(net, set()).update(nodes)
    return nets


def normalize_cad_nets(cad: Any) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    if isinstance(cad, dict):
        for net, nodes in cad.items():
            if isinstance(nodes, (list, set, tuple)):
                nets[net] = {str(n).replace("-", ".") for n in nodes}
            else:
                nets[net] = set()
    elif isinstance(cad, list):
        for item in cad:
            if isinstance(item, dict) and "net" in item:
                nodes = item.get("nodes") or item.get("pins") or []
                nets[item["net"]] = {str(n).replace("-", ".") for n in nodes}
    return nets


def compare_netlists(cad_nets: Dict[str, Set[str]],
                     fab_nets: Dict[str, Set[str]]) -> dict:
    cad_nets = cad_nets or {}
    fab_nets = fab_nets or {}
    all_nets = sorted(set(cad_nets) | set(fab_nets))
    net_only_cad = sorted(set(cad_nets) - set(fab_nets))
    net_only_fab = sorted(set(fab_nets) - set(cad_nets))
    node_open: List[dict] = []
    node_short: List[dict] = []
    matched = 0
    for net in all_nets:
        c = cad_nets.get(net, set())
        f = fab_nets.get(net, set())
        if net in cad_nets and net in fab_nets:
            matched += 1
        for node in sorted(c - f):
            node_open.append({"net": net, "node": node,
                              "reason": "CAD 有网络连接但制造数据无此连接 (疑似开路 Open)"})
        for node in sorted(f - c):
            node_short.append({"net": net, "node": node,
                               "reason": "制造数据有额外网络连接 (疑似短路 Short)"})

    passed = not (net_only_cad or net_only_fab or node_open or node_short)
    return {
        "cad_net_count": len(cad_nets),
        "fab_net_count": len(fab_nets),
        "matched_nets": matched,
        "net_only_cad": net_only_cad,
        "net_only_fab": net_only_fab,
        "node_open": node_open,
        "node_short": node_short,
        "open_count": len(node_open) + len(net_only_cad),
        "short_count": len(node_short) + len(net_only_fab),
        "passed": passed,
    }


def netlist_compare_issues(comparison: dict) -> List[dict]:
    issues: List[dict] = []
    counter = 0

    def add(rule, severity, desc, measured=None, required=None, unit="", suggestion=""):
        nonlocal counter
        counter += 1
        issues.append(make_issue(
            code=f"NET-{counter:02d}", category="网络比对", rule=rule,
            severity=severity, description=desc, measured=measured,
            required=required, unit=unit, suggestion=suggestion,
        ))

    for item in comparison.get("node_open", []):
        add("开路(Open)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中缺失连接",
            item["node"], "应连接", "", "核对网络/铺铜连接, 修正开路")
    for item in comparison.get("node_short", []):
        add("短路(Short)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中出现额外连接",
            item["node"], "无连接", "", "检查孤立铜/残铜, 修正短路")
    for net in comparison.get("net_only_cad", []):
        add("网络缺失", "高", f"CAD 网络 {net} 在制造数据中不存在",
            net, "需存在", "", "核对网络是否漏输出/被合并")
    for net in comparison.get("net_only_fab", []):
        add("多余网络", "高", f"制造数据出现 CAD 中不存在的网络 {net}",
            net, "不应存在", "", "核对多余网络来源")
    if not issues:
        issues.append(make_issue(code="NET-00", category="网络比对", rule="网络一致性",
                                 severity="低", description="网络表比对通过, 未发现开路/短路",
                                 suggestion="无需处理"))
    return issues


def analyze_fab(path: str) -> dict:
    data = read_fab_data(path)
    issues = integrity_check(data)
    stats = {
        "gerber_layers": [d["name"] for d in data if d["kind"] == "gerber"],
        "drill_files": [d["name"] for d in data if d["kind"] == "drill"],
        "odb_jobs": [d["name"] for d in data if d["kind"] == "odbpp"],
        "feature_count": sum(d.get("feature_count", 0) for d in data),
        "hole_count": sum(d.get("hole_count", 0) for d in data),
        "min_line_width_mm": _min_attr(data, "min_line_width_mm"),
        "min_hole_mm": _min_attr(data, "min_hole_mm"),
        "nets": collect_fab_nets(data),
    }
    return {"data": data, "issues": issues, "stats": stats}


def _min_attr(data, key):
    vals = [d.get(key) for d in data if d.get(key) is not None]
    return round(min(vals), 4) if vals else None


def build_fab_report(fab_result: dict, comparison: Optional[dict] = None,
                     path: str = "") -> dict:
    """将制造数据 DFM 结果整理为与 dfm_report.generate_dfm_excel 兼容的结构"""
    try:
        from dfm_rules import summarize_issues, compute_risk_score
    except Exception:                 # noqa: BLE001
        def summarize_issues(_issues):
            return {}

        def compute_risk_score(_issues):
            return {}
    issues = list(fab_result.get("issues", []))
    if comparison is not None:
        issues += netlist_compare_issues(comparison)
    stats = fab_result.get("stats", {})
    info = {
        "manufacturing": {
            "Gerber图层数": len(stats.get("gerber_layers", [])),
            "钻孔文件数": len(stats.get("drill_files", [])),
            "ODB++工程数": len(stats.get("odb_jobs", [])),
            "图形总数": stats.get("feature_count"),
            "孔总数": stats.get("hole_count"),
            "最小线宽": stats.get("min_line_width_mm"),
            "最小孔径": stats.get("min_hole_mm"),
        },
        "assembly": {},
        "capability_notes": {},
    }
    return {
        "factory_label": "制造数据",
        "board_type_label": os.path.basename(os.path.normpath(path)) if path else "Fabrication",
        "capability_label": "Gerber/ODB++/NC Drill",
        "copper_um": None,
        "issues": issues,
        "issue_summary": summarize_issues(issues),
        "risk": compute_risk_score(issues),
        "info_stats": info,
    }


# ============================================================
# fab_data -> 统一 design schema (复用 BRD 的几何 DFM/开短路/报告)
# ============================================================
def fab_to_design(fab_data: List[dict], board_thickness: Optional[float] = None) -> dict:
    """把 Gerber/ODB++/NC Drill 解析结果转换为与 SKILL/extracta 导出相同的
    design schema, 从而复用 dfm_geometry / dfm_netlist / brd_dfm / dfm_report。
    - draw/arc  -> clines (真实逐段线宽, etch_width_is_average=False)
    - flash     -> pads (焊盘/过孔焊盘, 含 net/refdes/pin/XY)
    - region    -> shapes (铺铜, 取包围盒)
    - drill 孔  -> vias (孔径; 就近匹配 flash 取网络/焊盘尺寸算孔环)
    - 网表节点  -> 由带 net+refdes+pin 的 flash 汇总 (含 XY)
    """
    clines: List[dict] = []
    pads: List[dict] = []
    vias: List[dict] = []
    shapes: List[dict] = []
    layers_seen: List[str] = []
    net_nodes: Dict[str, List[dict]] = {}
    pad_by_loc: Dict[Tuple[float, float], dict] = {}
    drill_holes: List[dict] = []
    outline_pts: List[List[float]] = []
    all_x: List[float] = []
    all_y: List[float] = []

    def _note_layer(nm):
        if nm and nm not in layers_seen:
            layers_seen.append(nm)

    def _is_outline(layer: str) -> bool:
        return bool(_OUTLINE_RE.search(layer or ""))

    for d in fab_data:
        kind = d.get("kind")
        if kind in ("gerber", "odbpp"):
            feats = d.get("primitives") or d.get("features") or []
            for p in feats:
                t = p.get("type")
                net = p.get("net", "") or ""
                layer = p.get("layer", "") or d.get("name", "") or ""
                _note_layer(layer)
                if t in ("draw", "arc"):
                    x1, y1, x2, y2 = p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2")
                    if None in (x1, y1, x2, y2):
                        continue
                    if _is_outline(layer):
                        outline_pts += [[x1, y1], [x2, y2]]
                        all_x += [x1, x2]; all_y += [y1, y2]
                        continue
                    clines.append({"net": net, "layer": layer,
                                   "width": p.get("width"),
                                   "points": [[x1, y1], [x2, y2]]})
                    all_x += [x1, x2]; all_y += [y1, y2]
                elif t == "flash":
                    x, y = p.get("x1"), p.get("y1")
                    if x is None or y is None:
                        continue
                    w = p.get("width") or 0.3
                    pad = {"refdes": p.get("refdes", "") or "", "pin": p.get("pin", "") or "",
                           "net": net, "x": x, "y": y, "layer": layer, "padstack": "",
                           "is_smd": False, "pad_w": w, "pad_h": w, "drill_mm": 0}
                    pads.append(pad)
                    pad_by_loc[(round(x, 2), round(y, 2))] = pad
                    all_x.append(x); all_y.append(y)
                    if net and pad["refdes"]:
                        net_nodes.setdefault(net, []).append(
                            {"refdes": pad["refdes"], "pin": pad["pin"],
                             "x": x, "y": y, "layer": layer})
                elif t == "region":
                    pts = p.get("points") or []
                    if len(pts) >= 3:
                        xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
                        shapes.append({"net": net, "layer": layer,
                                       "bbox": [min(xs), min(ys), max(xs), max(ys)]})
                        all_x += xs; all_y += ys
            # gerber/odb 自带 net->节点名 (无XY) 作为网表补充
            for nm, nodes in (d.get("nets") or {}).items():
                bucket = net_nodes.setdefault(nm, [])
                have = {f"{x['refdes']}.{x['pin']}" for x in bucket}
                for node in nodes:
                    if node not in have:
                        ref, _, pin = str(node).partition(".")
                        bucket.append({"refdes": ref, "pin": pin,
                                       "x": None, "y": None, "layer": ""})
        elif kind == "drill":
            for h in d.get("holes", []):
                if h.get("x") is None:
                    continue
                drill_holes.append(h)
                all_x.append(h["x"]); all_y.append(h["y"])

    # 钻孔 -> 过孔 (就近匹配 flash 取网络/焊盘尺寸)
    for h in drill_holes:
        x, y, dia = h.get("x"), h.get("y"), h.get("diameter_mm")
        pad = pad_by_loc.get((round(x, 2), round(y, 2)))
        vias.append({
            "net": (pad or {}).get("net", "") or "",
            "x": x, "y": y, "drill_mm": dia,
            "pad_mm": (pad or {}).get("pad_w"),
            "padstack": "", "start_layer": "", "end_layer": "",
            "type": "through",
        })

    # 板框: 仅当检测到专门轮廓层时使用真实轮廓 (否则留空, 不做铜箔到板边检查,
    # 避免以铜箔外包框当板边导致全部误判为"到边0mm")
    outline = outline_pts

    nets = [{"name": nm, "nodes": [nd for nd in nodes if nd.get("x") is not None]}
            for nm, nodes in sorted(net_nodes.items())]

    return {
        "format": "brd-dfm-export",
        "version": "1.0",
        "tool": "Gerber/ODB++/NC Drill",
        "source": "fabrication",
        "units": "mm",
        "source_units": "mm",
        "connectivity_source": "",            # 无 Allegro 连通性 -> 走几何开短路
        "etch_width_is_average": False,       # Gerber 线宽为逐段实测
        "board": {
            "name": "Fabrication",
            "revision": "",
            "thickness_mm": board_thickness,
            "layer_count": len(layers_seen),
        },
        "outline": outline,
        "stackup": [{"layer": l, "type": "signal"} for l in layers_seen],
        "nets": nets,
        "components": [],
        "clines": clines,
        "vias": vias,
        "pads": pads,
        "shapes": shapes,
        "testpoints": [],
        "opens": [],
        "drc": [],
        "net_avg_width_mm": {},
    }
