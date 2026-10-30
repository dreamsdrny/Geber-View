# -*- coding: utf-8 -*-
"""P8: 纯 Python 解析器提速 (快速路径 + 直写 PrimStore)

目标: 2~4x。手段:
  1. 标准坐标命令走一条单正则快速路径, 不再 upper()/两次 finditer/逐 token group()
  2. 图元直接 push 进 PrimStore (array), 不建 dict、不 meta()、不 d.update()
  3. 坐标换算内联 (int/int_pow), 变换在无镜像/无旋转时内联
  4. 光圈线宽按光圈码缓存

任何不符合标准形式的命令都原样落到原慢路径 -> 语义不变。
等价性由 tools/eq_check.py 逐字节对拍 (record 全 double 位级比较)。
"""
import io
import sys

R = "gerber_reader.py"

FAST_BLOCK = '''        # ---------------- 快速路径: 标准坐标命令 ----------------
        # 覆盖绝大多数命令 (X/Y/I/J + D01..D03, 可带 G01..G03): 单次正则 + 直写
        # PrimStore, 不建 dict。形式不符的一律落到下面的慢路径, 语义完全等价。
        if kind == "cmd" and sr is None:
            m = _FAST_RE.match(tok[1])
            if m is not None and (m.group(2) is not None or m.group(3) is not None):
                s_g = m.group(1)
                s_x = m.group(2)
                s_y = m.group(3)
                s_i = m.group(4)
                s_j = m.group(5)
                op = int(m.group(6))
                if s_g is not None:
                    interp = int(s_g)          # G01/G02/G03 -> 1/2/3
                cur_op = op
                # ---- 坐标字段 -> 文件单位 (与 _coord_value 位级相同) ----
                if s_x is None:
                    x_raw = None
                elif zero_mode == "T":
                    x_raw = _coord_t(s_x, fsta_total, pow10)
                else:
                    x_raw = int(s_x) / pow10
                if s_y is None:
                    y_raw = None
                elif zero_mode == "T":
                    y_raw = _coord_t(s_y, fsta_total, pow10)
                else:
                    y_raw = int(s_y) / pow10
                if incremental:
                    if x_raw is not None:
                        x_raw += rx
                    if y_raw is not None:
                        y_raw += ry
                nx_raw = rx if x_raw is None else x_raw
                ny_raw = ry if y_raw is None else y_raw
                # ---- 坐标范围 (等价 track()) ----
                if span[0] is None:
                    span[0] = span[2] = nx_raw
                    span[1] = span[3] = ny_raw
                else:
                    if nx_raw < span[0]:
                        span[0] = nx_raw
                    elif nx_raw > span[2]:
                        span[2] = nx_raw
                    if ny_raw < span[1]:
                        span[1] = ny_raw
                    elif ny_raw > span[3]:
                        span[3] = ny_raw
                # ---- 变换 (无镜像/无旋转时内联, 与 pt() 同序运算) ----
                uf = unit_factor
                if tf_plain:
                    xs = img_scale[0]
                    ys = img_scale[1]
                    xm = nx_raw * uf * xs + img_offset[0] * uf
                    ym = ny_raw * uf * ys + img_offset[1] * uf
                    cx_mm = rx * uf * xs + img_offset[0] * uf
                    cy_mm = ry * uf * ys + img_offset[1] * uf
                else:
                    xm, ym = pt(nx_raw, ny_raw)
                    cx_mm, cy_mm = pt(rx, ry)
                if in_region:
                    region_pts.append((xm, ym))
                    rx, ry = nx_raw, ny_raw
                    continue
                if op == 1:
                    if interp == 1:
                        # ---- 直线: 直接落库 ----
                        ap = cur_ap
                        w = apw_cache.get(ap)
                        if w is None and ap:
                            w = ap_width(ap)
                            if w is not None:
                                apw_cache[ap] = w
                        res.primitives.push(
                            PRIM_DRAW | (PRIM_CLEAR if cur_negative else 0),
                            cx_mm, cy_mm, xm, ym, w or 0.0, ap or 0, 0.0, 0.0,
                            cur_net, cur_refdes, cur_pin)
                        res.feature_count += 1
                        if w and (res.min_width is None or w < res.min_width):
                            res.min_width = w
                        if ap:
                            res.ap_usage[ap] = res.ap_usage.get(ap, 0) + 1
                        if cur_net and cur_refdes and cur_pin:
                            res.nets.setdefault(cur_net, set()).add(
                                cur_refdes + "." + cur_pin)
                        b = res.bbox
                        if b[0] is None:
                            b[0] = b[2] = cx_mm
                            b[1] = b[3] = cy_mm
                        else:
                            if cx_mm < b[0]:
                                b[0] = cx_mm
                            elif cx_mm > b[2]:
                                b[2] = cx_mm
                            if cy_mm < b[1]:
                                b[1] = cy_mm
                            elif cy_mm > b[3]:
                                b[3] = cy_mm
                        if xm < b[0]:
                            b[0] = xm
                        elif xm > b[2]:
                            b[2] = xm
                        if ym < b[1]:
                            b[1] = ym
                        elif ym > b[3]:
                            b[3] = ym
                    else:
                        do_arc(cx_mm, cy_mm, xm, ym,
                               _coord_value(s_i, int_digits, dec_digits, zero_mode),
                               _coord_value(s_j, int_digits, dec_digits, zero_mode),
                               interp == 2, cur_ap)
                elif op == 3:
                    ap = cur_ap
                    a = res.apertures.get(ap) if ap is not None else None
                    if a is not None and not a.get("macro"):
                        w = apw_cache.get(ap)
                        if w is None:
                            w = ap_width(ap)
                            if w is not None:
                                apw_cache[ap] = w
                        res.primitives.push(
                            PRIM_FLASH | (PRIM_CLEAR if cur_negative else 0),
                            xm, ym, xm, ym, w or 0.0, ap, 0.0, 0.0,
                            cur_net, cur_refdes, cur_pin)
                        res.feature_count += 1
                        if w and (res.min_width is None or w < res.min_width):
                            res.min_width = w
                        res.ap_usage[ap] = res.ap_usage.get(ap, 0) + 1
                        if cur_net and cur_refdes and cur_pin:
                            res.nets.setdefault(cur_net, set()).add(
                                cur_refdes + "." + cur_pin)
                        b = res.bbox
                        if b[0] is None:
                            b[0] = b[2] = xm
                            b[1] = b[3] = ym
                        else:
                            if xm < b[0]:
                                b[0] = xm
                            elif xm > b[2]:
                                b[2] = xm
                            if ym < b[1]:
                                b[1] = ym
                            elif ym > b[3]:
                                b[3] = ym
                    else:
                        do_flash(xm, ym, ap)      # 未定义/宏光圈: 走原逻辑
                rx, ry = nx_raw, ny_raw
                continue

'''

PATCHES = [
    # ---------- _coord_t ----------
    ('''def _coord_value(raw: str''',
     '''def _coord_t(raw: str, total: int, pow10: int) -> float:
    """尾随零省略 (FSTA) 的快速坐标换算, 与 _coord_value 位级相同"""
    neg = raw[0] == "-"
    dig = raw[1:] if neg else raw
    if len(dig) < total:
        dig = dig.ljust(total, "0")[:total]
    elif len(dig) > total:
        dig = dig[:total]
    v = int(dig) / pow10
    return -v if neg else v


def _coord_value(raw: str'''),
    # ---------- PrimStore.push ----------
    ('''    def scaled(self, factor):''',
     '''    def push(self, code, x1, y1, x2, y2, width, aperture, i, j,
             net="", ref="", pin=""):
        """直接追加一条图元 (不经过 dict), 供解析器热路径使用"""
        n = self.n
        self.rec.extend((float(code), x1, y1, x2, y2, width,
                         float(aperture), i, j))
        if net or ref or pin:
            self.extras[n] = (None, net, ref, pin)
        self.n = n + 1

    def scaled(self, factor):'''),
    # ---------- _FAST_RE ----------
    ('''_XYIJ_RE = re.compile(r"([XYIJ])(" + _NUM + r")")''',
     '''_XYIJ_RE = re.compile(r"([XYIJ])(" + _NUM + r")")
# 标准坐标命令: [G01-03] X Y I J D01-03  (全部可选, 但至少要有一个坐标)
_FAST_RE = re.compile(
    r"^(?:G0?([123]))?(?:X(-?\\d+))?(?:Y(-?\\d+))?(?:I(-?\\d+))?"
    r"(?:J(-?\\d+))?D0?([123])$")'''),
    # ---------- 状态变量 ----------
    ('''    sr: Optional[dict] = None               # 当前 %SR 拼版
    sr_buf: List[dict] = []''',
     '''    sr: Optional[dict] = None               # 当前 %SR 拼版
    sr_buf: List[dict] = []
    # ---- 快速路径用的缓存量 (只在相关状态变化时更新) ----
    pow10 = 10 ** dec_digits                # 坐标缩放 = 1/pow10
    fsta_total = int_digits + dec_digits    # FSTA 左对齐补零总位数
    tf_plain = True                         # 无镜像/无旋转 -> 变换可内联
    apw_cache: Dict[int, float] = {}        # 光圈码 -> 线宽 mm'''),
    # ---------- %FS: 更新 pow10 ----------
    ('''                res.int_digits, res.dec_digits = int_digits, dec_digits
                incremental = incremental or coord_mode == "I"''',
     '''                res.int_digits, res.dec_digits = int_digits, dec_digits
                pow10 = 10 ** dec_digits
                fsta_total = int_digits + dec_digits
                incremental = incremental or coord_mode == "I"'''),
    # ---------- %MO: 光圈线宽缓存失效 ----------
    ('''                res.unit = unit
                res.unit_source = "declared(%MO)"
                continue''',
     '''                res.unit = unit
                res.unit_source = "declared(%MO)"
                apw_cache.clear()
                continue'''),
    # ---------- %SF ----------
    ('''                img_scale = [a if a else 1.0, b if b else (a if a else 1.0)]
                continue''',
     '''                img_scale = [a if a else 1.0, b if b else (a if a else 1.0)]
                apw_cache.clear()
                continue'''),
    # ---------- %MI / %IR: 变换是否可内联 ----------
    ('''                img_mirror = [(nums[0] if nums else "0") == "1",
                              (nums[1] if len(nums) > 1 else "0") == "1"]
                continue''',
     '''                img_mirror = [(nums[0] if nums else "0") == "1",
                              (nums[1] if len(nums) > 1 else "0") == "1"]
                tf_plain = not (img_mirror[0] or img_mirror[1] or img_rot)
                continue'''),
    ('''                img_rot = int(v or 0) % 360
                continue''',
     '''                img_rot = int(v or 0) % 360
                tf_plain = not (img_mirror[0] or img_mirror[1] or img_rot)
                continue'''),
    # ---------- G70 / G71 ----------
    ('''                elif gi == 70:
                    unit, unit_declared = "inch", True
                    unit_factor = INCH_TO_MM
                    res.unit, res.unit_source = unit, "declared(G70)"''',
     '''                elif gi == 70:
                    unit, unit_declared = "inch", True
                    unit_factor = INCH_TO_MM
                    res.unit, res.unit_source = unit, "declared(G70)"
                    apw_cache.clear()'''),
    ('''                elif gi == 71:
                    unit, unit_declared = "mm", True
                    unit_factor = 1.0
                    res.unit, res.unit_source = unit, "declared(G71)"''',
     '''                elif gi == 71:
                    unit, unit_declared = "mm", True
                    unit_factor = 1.0
                    res.unit, res.unit_source = unit, "declared(G71)"
                    apw_cache.clear()'''),
    # ---------- 插入快速路径 ----------
    ('''        # ---------------- 扩展命令 (%...%) ----------------
        if kind == "ext":''',
     FAST_BLOCK + '''        # ---------------- 扩展命令 (%...%) ----------------
        if kind == "ext":'''),
]


def main():
    text = io.open(R, encoding="utf-8").read()
    for i, (old, new) in enumerate(PATCHES, 1):
        c = text.count(old)
        if c != 1:
            print("[FAIL] patch #%d: hit %d" % (i, c))
            print("       ", old.strip().splitlines()[0][:88])
            return 1
        text = text.replace(old, new, 1)
    io.open(R, "w", encoding="utf-8", newline="").write(text)
    print("[OK] %s: %d patches, lines %d" % (R, len(PATCHES), text.count("\n") + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
