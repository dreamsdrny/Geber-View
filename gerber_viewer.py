"""
Evan Gerber View: Gerber 查看器 (KiCad 风格) + 多层 A/B 叠加对比 + 自动差异定位
=========================================================================
功能:
  - 导入多个 Gerber / 钻孔 (线路/阻焊/丝印/钻孔等), 按【层类型】自动分配不同颜色
  - 缩放(滚轮)/平移(右键拖动)/适应窗口; 选中图层可单独拖动对齐
  - 多层 A/B 对比: A/B 各可导入【多层】(多文件或整个目录), 自动按中心对齐
  - 自动定位差异: 栅格化 A/B 材料 mask 做 XOR, 输出差异位置(XY)+信息, 图上高亮
  - 网络高亮: 画布双击焊盘/走线 -> 高亮整条网络 (读 %TO.N/%TO.P 属性)
  - 单层 Solo 隔离、测距/测角度、导出 PNG
  - (原 DFM 检查/报告功能已整体移除, 不再有相关入口)

性能 (针对卡顿/残影/死机的重构):
  - 解析在【后台线程】, 导入大文件不冻结界面
  - 每层渲染为【独立位图缓存】(后台线程), 显示时只按视口【裁剪+缩放+合成】到
    一张【画布大小】的图像, 画布上只有 1 个图元 → 无残影, 内存有上界, 不会死机
  - 缩放/平移/拖动复用缓存位图 (仅 C 级 crop/resize/paste), 停止后再后台重渲清晰版
  - 隐藏图层释放其位图缓存, 控制内存

复用 gerber_reader.parse_gerber / parse_excellon 的几何数据。
"""
import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gerber_reader import parse_gerber, parse_excellon
from branding import win_title

def _outside(pts, clip, margin=0):
    """折线/多边形是否完全落在裁剪矩形之外"""
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return (max(xs) + margin < clip[0] or min(xs) - margin > clip[2]
            or max(ys) + margin < clip[1] or min(ys) - margin > clip[3])


# ---------------- 颜色方案 ----------------
_PALETTE = ["#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa",
            "#00acc1", "#6d4c41", "#3949ab", "#c0ca33", "#d81b60"]

# 按层类型 (KiCad 风格) 分配颜色
_TYPE_COLOR = {
    ("copper", "top"): "#e0483c",
    ("copper", "bottom"): "#2f7fe0",
    ("soldermask", "top"): "#8b52d6",
    ("soldermask", "bottom"): "#47c4b0",
    ("silkscreen", "top"): "#e9e24c",
    ("silkscreen", "bottom"): "#d05fa8",
    ("paste", "top"): "#9aa7ad",
    ("paste", "bottom"): "#b0bec5",
    ("profile", ""): "#39d7ff",
    ("drill", "pth"): "#ffffff",
    ("drill", "npth"): "#ffc23d",
}
_COPPER_INNER = ["#3fb54a", "#b06ad1", "#e0a02f", "#1fb3a6",
                 "#d1499b", "#5b8def", "#c9c01f", "#e0662f"]

# A/B 对比: A 暖色系, B 冷色系 (同组内多层也各不相同)
_WARM = ["#ff4d4f", "#ff9f43", "#ffd166", "#ef476f", "#f78c6b", "#e63946", "#ff8fab", "#fb8500"]
_COOL = ["#3fa9ff", "#06d6a0", "#4cc9f0", "#5b8def", "#00b4d8", "#48cae4", "#2ec4b6", "#4361ee"]

HL_RGB = (255, 214, 10)          # 网络高亮色
BG_RGB = (15, 17, 21)
# 每层缓存改为 [L 灰度掩膜 + 固定颜色] 而不是 RGBA:
#   - 合成时只重采样覆盖率, 不会把透明区的黑色混进边缘 (RGBA 未预乘会产生黑边/发灰)
#   - 1 字节/像素, 同样内存预算下分辨率可以更高 -> 放大时更清晰
MAX_BASE = 4096          # 单层缓存位图最长边上限 (px)
QUALITY = 1.0            # 缓存位图按显示比例渲染 -> 静止时合成可 1:1 免重采样 (平移更流畅)
SS_MAX_PIXELS = 4_500_000  # 最终位图不超过该像素数时按 2× 超采样绘制 (抗锯齿)
DIFF_PX = 1500           # 差异栅格化最长边 (px)
DIFF_GRID = 200          # 差异定位网格 (每边单元数)
DIFF_THRESH = 0.28       # 单元覆盖率差阈值 -> 判为差异
MAX_MARKERS = 600        # 图上最多绘制的差异标记数 (太多会拖慢每帧合成)
BUDGET_BYTES = 320_000_000  # 全部图层掩膜缓存上限 (1 字节/像素), 超出则按 LRU 释放隐藏层

# 常见「单位/坐标格式」错误倍数: 不同 CAM 导出时英寸↔毫米、小数位差一位等
_KNOWN_FACTORS = (1000.0, 254.0, 100.0, 25.4, 10.0, 2.54, 1.0 / 2.54,
                  0.1, 1.0 / 25.4, 0.01, 1.0 / 254.0, 0.001)


def _match_known_factor(ratio, tol=0.02):
    """比值是否命中常见单位/格式错误倍数 (命中则返回该标准倍数)"""
    if not ratio or ratio <= 0:
        return None
    for f in _KNOWN_FACTORS:
        if abs(ratio - f) / f <= tol:
            return f
    return None


def _hex_rgb(h):
    h = (h or "#2f7ed8").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (47, 126, 216)


class _GridIndex:
    """均匀网格索引: 图元按包围盒放进相交的格子

    用途: (1) 深放大时只取视口附近的候选图元; (2) 点选焊盘做命中测试。
    内存: 索引项 array('i'), 总量与图元数同量级 (每图元 1~2 项)。
    """

    __slots__ = ("x0", "y0", "cw", "ch", "nx", "ny", "off", "items",
                 "n", "seen", "gen")

    def __init__(self, store, data, bbox, target_per_cell=3.0):
        import array

        x0, y0, x1, y1 = bbox
        self.x0, self.y0 = x0, y0
        self.n = store.n
        w = max(x1 - x0, 1e-6)
        h = max(y1 - y0, 1e-6)
        n = max(1, store.n)
        cells = max(1, int(n / max(target_per_cell, 1e-6)))
        # 按长短边分配格数, 保持格子接近正方形
        nx = max(1, min(1024, int(math.sqrt(cells * w / h) + 0.5)))
        ny = max(1, min(1024, int(cells / nx + 0.5)))
        self.nx, self.ny = nx, ny
        self.cw = w / nx
        self.ch = h / ny

        # 光圈最大尺寸 (flash 的包围盒要用)
        ap_max = {}
        for code, ap in (data.get("apertures") or {}).items():
            params = [v for v in (ap.get("params") or [])
                      if isinstance(v, (int, float))]
            if params:
                ap_max[code] = max(abs(params[0]), abs(params[1]) if len(params) > 1 else 0.0) \
                    * (25.4 if data.get("unit") == "inch" else 1.0)
        rec = store.rec
        extras = store.extras

        boxes = array.array("f")
        counts = array.array("i", bytes(4 * nx * ny))
        for i in range(n):
            b = i * 9
            bb = _prim_bbox(rec, b, extras, i, ap_max)
            if bb is None:
                boxes.extend((0.0, 0.0, -1.0, -1.0))
                continue
            boxes.extend(bb)
            for cy in range(self._c0(bb[1], self.y0, self.ch, ny),
                            self._c1(bb[3], self.y0, self.ch, ny) + 1):
                row = cy * nx
                for cx in range(self._c0(bb[0], self.x0, self.cw, nx),
                                self._c1(bb[2], self.x0, self.cw, nx) + 1):
                    counts[row + cx] += 1

        off = array.array("i", bytes(4 * (nx * ny + 1)))
        total = 0
        for c in range(nx * ny):
            off[c] = total
            total += counts[c]
        off[nx * ny] = total
        items = array.array("i", bytes(4 * total))
        cur = array.array("i", off)
        for i in range(n):
            k = i * 4
            if boxes[k + 2] < boxes[k]:
                continue
            for cy in range(self._c0(boxes[k + 1], self.y0, self.ch, ny),
                            self._c1(boxes[k + 3], self.y0, self.ch, ny) + 1):
                row = cy * nx
                for cx in range(self._c0(boxes[k], self.x0, self.cw, nx),
                                self._c1(boxes[k + 2], self.x0, self.cw, nx) + 1):
                    c = row + cx
                    items[cur[c]] = i
                    cur[c] += 1
        self.off = off
        self.items = items
        self.seen = array.array("i", bytes(4 * n))
        self.gen = 0

    @staticmethod
    def _c0(v, base, size, cnt):
        c = int((v - base) / size)
        return 0 if c < 0 else (cnt - 1 if c >= cnt else c)

    @staticmethod
    def _c1(v, base, size, cnt):
        return _GridIndex._c0(v, base, size, cnt)

    def query(self, rx0, ry0, rx1, ry1):
        """返回与矩形相交的图元序号 (升序, 保持绘制顺序); 矩形极大时返回 None"""
        if self.n <= 0:
            return []
        gx0 = self._c0(min(rx0, rx1), self.x0, self.cw, self.nx)
        gx1 = self._c0(max(rx0, rx1), self.x0, self.cw, self.nx)
        gy0 = self._c0(min(ry0, ry1), self.y0, self.ch, self.ny)
        gy1 = self._c0(max(ry0, ry1), self.y0, self.ch, self.ny)
        # 覆盖面积接近全层时没必要走索引
        if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) >= self.nx * self.ny * 0.6:
            return None
        seen = self.seen
        gen = self.gen + 1
        self.gen = gen
        items = self.items
        off = self.off
        nx = self.nx
        out = []
        ap = out.append
        for cy in range(gy0, gy1 + 1):
            row = cy * nx
            for cx in range(gx0, gx1 + 1):
                c = row + cx
                for k in range(off[c], off[c + 1]):
                    i = items[k]
                    if seen[i] != gen:
                        seen[i] = gen
                        ap(i)
        out.sort()
        return out


def _prim_bbox(rec, b, extras, i, ap_max):
    """图元的包围盒 (本地坐标, 已含线宽/焊盘尺寸), 供建索引与命中测试"""
    code = int(rec[b])
    t = code & 7
    x1 = rec[b + 1]; y1 = rec[b + 2]
    x2 = rec[b + 3]; y2 = rec[b + 4]
    wd = rec[b + 5]
    if t == 1:
        r = ap_max.get(int(rec[b + 6]), max(wd, 0.2)) * 0.5 + 0.05
        return (x1 - r, y1 - r, x1 + r, y1 + r)
    if t == 2:
        cx = x1 + rec[b + 7]; cy = y1 + rec[b + 8]
        r = math.hypot(x1 - cx, y1 - cy) + max(wd, 0.0) * 0.5 + 0.05
        return (cx - r, cy - r, cx + r, cy + r)
    if t == 3:
        e = extras.get(i)
        pts = e[0] if e else None
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    r = max(wd, 0.0) * 0.5 + 0.05
    return (min(x1, x2) - r, min(y1, y2) - r, max(x1, x2) + r, max(y1, y2) + r)


class GerberLayer:
    def __init__(self, name, data, color, group=""):
        self.name = name
        self.data = data
        self.raw_data = data                 # 原始解析结果 (比例校正前)
        self.scale_factor = 1.0              # 已应用的人工/自动比例校正
        self.auto_scaled = False             # 是否由程序自动校正过比例
        self.color = color
        self.group = group                   # "" | "A" | "B"
        self.visible = True
        self.offset = [0.0, 0.0]
        self.mirror_x = False
        self.kind = data.get("kind", "gerber")
        self.role = data.get("_role") or {}
        # 位图缓存 (仅主线程读取; 由后台线程渲染后经 UI 队列回填)
        self.base_img = None               # PIL RGBA
        self.base_scale = 0.0              # px / mm
        self.base_bbox = None              # 本地(镜像已应用)包围盒
        self.token = 0                     # 渲染令牌 (丢弃过期结果)
        self.pending = False               # 是否有在途渲染任务
        self.lru = 0.0
        self.solid = None                  # 遗留字段 (合成已改为直接按颜色贴图)
        # 高清块: 放大到全层缓存分辨率不够时, 只按当前视口渲染一块 (分辨率=屏幕),
        # 叠在粗缓存上显示 -> 放大后也是 1:1, 不再靠拉伸。
        self.detail_img = None
        self.detail_scale = 0.0            # px / mm
        self.detail_bbox = None            # 本地(镜像已应用)包围盒
        self.detail_token = 0
        self.detail_pending = False
        self.index = None               # 空间索引 (深放大/点选时按需建)
        self.index_sig = None
        self.net_map = None             # 网络名 -> 图元序号 (点选时按需建)

    # ---------------- 比例校正 ----------------
    def apply_scale(self, factor, auto=False):
        """按统一比例缩放本层几何数据 (用于不同 CAM 导出的单位/格式差异)"""
        if not factor or abs(factor - 1.0) < 1e-9:
            return
        from gerber_reader import scale_fab_data
        self.scale_factor *= factor
        role = (self.data or {}).get("_role") or self.role
        self.data = scale_fab_data(self.raw_data, self.scale_factor)
        self.data["_role"] = role
        self.role = role
        self.auto_scaled = self.auto_scaled or auto
        self.base_img = None
        self.base_bbox = None
        self.solid = None
        self.detail_img = None
        self.detail_token += 1
        self.detail_pending = False
        self.token += 1
        self.pending = False

    def reset_scale(self):
        if abs(self.scale_factor - 1.0) < 1e-9:
            return
        self.scale_factor = 1.0
        self.auto_scaled = False
        role = (self.data or {}).get("_role") or self.role
        self.data = self.raw_data
        self.data["_role"] = role
        self.base_img = None
        self.base_bbox = None
        self.solid = None
        self.detail_img = None
        self.detail_token += 1
        self.detail_pending = False
        self.token += 1
        self.pending = False

    def unit_label(self):
        d = self.data or {}
        src = str(d.get("source_unit") or d.get("unit") or "")
        txt = "in" if src == "inch" else "mm"
        if d.get("format"):
            txt += f" {d['format']}"
        us = str(d.get("unit_source") or "")
        if "inferred" in us or "default" in us:
            txt += "?"                        # 单位/格式为推断结果
        if abs(self.scale_factor - 1.0) > 1e-9:
            txt += f" ×{self.scale_factor:g}"
        return txt

    def warn_text(self):
        return "; ".join(str(w) for w in ((self.data or {}).get("warnings") or []))

    def detail_text(self):
        d = self.data or {}
        b = self.local_bbox()
        size = f"{b[2] - b[0]:.2f}×{b[3] - b[1]:.2f}mm" if b else "-"
        bits = [f"单位/格式: {self.unit_label()}", f"图幅: {size}"]
        if d.get("software"):
            bits.append(f"来源软件: {d['software']}")
        if d.get("hole_count") is not None and self.kind == "drill":
            bits.append(f"孔数: {d['hole_count']}")
        else:
            bits.append(f"图元: {d.get('feature_count', 0)}")
        if abs(self.scale_factor - 1.0) > 1e-9:
            bits.append(f"比例校正: ×{self.scale_factor:g}" + ("(自动)" if self.auto_scaled else ""))
        w = self.warn_text()
        if w:
            bits.append("提示: " + w)
        return "  |  ".join(bits)

    def _raw_bbox(self):
        b = self.data.get("bbox")
        if not b or b[0] is None:
            return None
        return list(b)

    def local_bbox(self):
        """不含偏移的包围盒 (镜像已应用), 用于位图缓存"""
        b = self._raw_bbox()
        if not b:
            return None
        minx, miny, maxx, maxy = b
        if self.mirror_x:
            minx, maxx = -maxx, -minx
        return [minx, miny, maxx, maxy]

    def bbox(self):
        lb = self.local_bbox()
        if not lb:
            return None
        dx, dy = self.offset
        return [lb[0] + dx, lb[1] + dy, lb[2] + dx, lb[3] + dy]

    def type_label(self):
        r = self.role or {}
        k = r.get("kind") or ("drill" if self.kind == "drill" else "other")
        side = r.get("side") or ""
        names = {"copper": "铜", "soldermask": "阻焊", "silkscreen": "丝印",
                 "paste": "锡膏", "profile": "外形", "drill": "钻孔", "other": "其它"}
        base = names.get(k, k)
        if k == "copper":
            idx = r.get("index")
            if side == "inner" and idx:
                return f"{base}·内{idx}"
        if side:
            sm = {"top": "顶", "bottom": "底", "inner": "内"}
            return f"{base}·{sm.get(side, side)}"
        return base


class GerberViewer:
    def __init__(self, master, title="Gerber 查看器 (叠加对比)"):
        self.win = tk.Toplevel(master)
        self.win.title(win_title(title))
        from win_dpi import apply_window
        apply_window(self.win, 1220, 780)

        self.layers = []
        self.selected = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._drag = None
        self._view_bbox = None
        self._pil = None
        self._photo = None                 # 保留引用防 GC
        self._img_item = None              # 画布上唯一的图像图元
        self.diff_points = []              # 自动差异定位结果
        self._group_idx = {"A": 0, "B": 0}
        self._batch = 0                    # 待解析文件计数
        self._batch_fit = False
        self._render_job = None
        self._settle_job = None
        self._lru_clock = 0.0
        self._frame_scale = None
        self._interactive = False           # 交互中(拖动/平移/缩放) → 用更快但略粗的重采样
        self._fast_preview = False          # 是否允许用"上一帧整图变换"做预览 (缩放/平移)
        self._last_img = None               # 上一帧完整合成结果 (不含选中框/标记)
        self._last_view = None              # 该帧对应的世界范围
        self._solo = None                   # 单层隔离 (只显示这一层)
        self._hl = None                     # 网络高亮 {net, items}
        self._measure_on = False
        self._measure_pts = []              # 测距点 (mm)
        self._measure_cur = None
        self._hover_job = None              # 坐标读数节流定时器
        self._hover_xy = None
        self._cfg_job = None                # 窗口尺寸变化防抖定时器

        self._stop = threading.Event()
        self._parse_q = queue.Queue()
        self._render_q = queue.Queue()
        self._ui_q = queue.Queue()

        self._build()
        self._start_workers()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- PIL 延迟加载 ----------------
    def _PIL(self):
        if self._pil is None:
            from PIL import Image, ImageDraw, ImageTk
            self._pil = (Image, ImageDraw, ImageTk)
        return self._pil

    def _ui_scale(self):
        """当前窗口的 DPI 缩放 (1.0 = 96 DPI), 用来把写死的像素尺寸换算成视觉尺寸"""
        try:
            from win_dpi import get_dpi
            return max(1.0, float(get_dpi(self.win)) / 96.0)
        except Exception:  # noqa: BLE001
            return 1.0

    # ---------------- UI ----------------
    def _build(self):
        top = ttk.Frame(self.win, padding=6)
        top.pack(fill=tk.X)
        ttk.Button(top, text="导入Gerber...", command=self._import_gerber).pack(side=tk.LEFT)
        ttk.Button(top, text="导入钻孔...", command=self._import_drill).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="导入全部(目录)...", command=self._import_dir).pack(side=tk.LEFT)
        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(top, text="对比A: 导入多层", command=lambda: self._compare_load("A")).pack(side=tk.LEFT)
        ttk.Button(top, text="对比B: 导入多层", command=lambda: self._compare_load("B")).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="自动定位差异", command=self._auto_diff).pack(side=tk.LEFT)
        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(top, text="清空", command=self._clear).pack(side=tk.LEFT)
        ttk.Button(top, text="导出PNG", command=self._export_png).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)

        bar = ttk.Frame(self.win, padding=(6, 0))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="适应", command=self.fit).pack(side=tk.LEFT)
        ttk.Button(bar, text="视图放大", command=lambda: self._zoom(1.25)).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="视图缩小", command=lambda: self._zoom(0.8)).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar, text="选中图层:").pack(side=tk.LEFT)
        ttk.Button(bar, text="居中", command=self._center_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="重置偏移", command=self._reset_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="镜像", command=self._mirror_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="改颜色", command=self._pick_color).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="上移", command=lambda: self._move_layer(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="下移", command=lambda: self._move_layer(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="删除", command=self._delete_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="对齐中心", command=self._align_center).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="对齐左下", command=self._align_bl).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        mb = ttk.Menubutton(bar, text="比例校正")
        mnu = tk.Menu(mb, tearoff=0)
        mnu.add_command(label="选中层 ×25.4  (英寸被当成毫米)",
                        command=lambda: self._scale_sel(25.4))
        mnu.add_command(label="选中层 ÷25.4  (毫米被当成英寸)",
                        command=lambda: self._scale_sel(1.0 / 25.4))
        mnu.add_command(label="选中层 ×10  (小数位少一位)", command=lambda: self._scale_sel(10.0))
        mnu.add_command(label="选中层 ÷10  (小数位多一位)", command=lambda: self._scale_sel(0.1))
        mnu.add_command(label="选中层 自定义比例...", command=self._scale_sel_custom)
        mnu.add_command(label="选中层 恢复原始比例", command=self._scale_sel_reset)
        mnu.add_separator()
        mnu.add_command(label="钻孔层对齐线路层 (自动识别 10/25.4 倍)",
                        command=lambda: self._auto_check_scales(True, True))
        mnu.add_command(label="B组尺寸匹配A组 (等比缩放)", command=self._scale_b_to_a)
        mnu.add_command(label="检查所有图层比例一致性", 
                        command=lambda: self._auto_check_scales(False, True))
        mb["menu"] = mnu
        mb.pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self._measure_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="测距/测角", variable=self._measure_var,
                        command=self._measure_toggle).pack(side=tk.LEFT)
        ttk.Button(bar, text="清除高亮", command=self._clear_overlays).pack(side=tk.LEFT, padx=4)
        self.move_mode = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="拖动移动图层", variable=self.move_mode).pack(side=tk.LEFT, padx=6)
        self.info = tk.StringVar(
            value="左键拖动=移动图层；右键拖动=平移；滚轮=缩放；画布双击=高亮整条网络；Esc=清除")
        ttk.Label(bar, textvariable=self.info, foreground="#555").pack(side=tk.RIGHT)

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(body, text="图层 (点『开/关』列开关)", padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y)
        btnbar = ttk.Frame(left)
        btnbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btnbar, text="全开", width=5, command=self._all_on).pack(side=tk.LEFT)
        ttk.Button(btnbar, text="一键全关", width=8, command=self._all_off).pack(side=tk.LEFT, padx=2)
        ttk.Button(btnbar, text="开选中", width=6, command=lambda: self._set_sel(True)).pack(side=tk.LEFT)
        ttk.Button(btnbar, text="关选中", width=6, command=lambda: self._set_sel(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btnbar, text="反选", width=5, command=self._invert).pack(side=tk.LEFT)
        ttk.Button(btnbar, text="Solo", width=5, command=self._solo_toggle).pack(side=tk.LEFT, padx=2)

        ui = self._ui_scale()
        style = ttk.Style(self.win)
        try:
            style.configure("GerberLayers.Treeview",
                            font=("Microsoft YaHei UI", 11), rowheight=int(28 * ui))
            style.configure("GerberLayers.Treeview.Heading",
                            font=("Microsoft YaHei UI", 10, "bold"))
        except Exception:
            pass
        # 行高随 DPI 放大, 可见行数相应减少 -> 总高度不变, 文字不会被裁掉
        self.tree = ttk.Treeview(left, columns=("st", "name", "type", "unit"), show="headings",
                                 height=max(8, int(26 / ui)), selectmode="extended",
                                 style="GerberLayers.Treeview")
        self.tree.heading("st", text="开/关")
        self.tree.heading("name", text="图层")
        self.tree.heading("type", text="类型")
        self.tree.heading("unit", text="单位/格式")
        self.tree.column("st", width=int(54 * ui), anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=int(196 * ui), anchor=tk.W)
        self.tree.column("type", width=int(72 * ui), anchor=tk.CENTER, stretch=False)
        self.tree.column("unit", width=int(86 * ui), anchor=tk.CENTER, stretch=False)
        self.tree.pack(side=tk.LEFT, fill=tk.Y)
        tsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("off", foreground="#8a8a8a")
        self.tree.tag_configure("warn", foreground="#c47b00")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_dbl)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.canvas = tk.Canvas(body, bg="#0f1115", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-3>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Double-Button-1>", self._highlight_at)
        self.canvas.bind("<Escape>", self._clear_overlays)
        self.canvas.bind_all("<Escape>", self._clear_overlays)
        self.canvas.bind("<Motion>", self._on_motion_measure, add="+")

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.win, textvariable=self.status, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------- 后台线程 ----------------
    def _start_workers(self):
        threading.Thread(target=self._parse_worker, daemon=True).start()
        threading.Thread(target=self._render_worker, daemon=True).start()
        self._poll()

    def _on_close(self):
        self._stop.set()
        try:
            self._parse_q.put(None)
            self._render_q.put(None)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def _parse_worker(self):
        while not self._stop.is_set():
            job = self._parse_q.get()
            if job is None:
                break
            paths, group = job
            for p in paths:
                if self._stop.is_set():
                    break
                base = os.path.basename(p)
                self._ui_q.put(("status", f"解析中: {base}"))
                try:
                    low = p.lower()
                    if low.endswith(".drl") or "drill" in low:
                        data = parse_excellon(p)
                    else:
                        data = parse_gerber(p, layer_name=base)
                    self._ui_q.put(("layer", data, p, group))
                except Exception as exc:  # noqa: BLE001
                    self._ui_q.put(("parse_err", base, str(exc)))
            self._ui_q.put(("parse_done", len(paths), group))

    def _render_worker(self):
        while not self._stop.is_set():
            job = self._render_q.get()
            if job is None:
                break
            try:
                kindjob = job[0]
                if kindjob == "base":
                    _, token, data, kind, lb, mirror, color, target = job
                    img, scale = self._render_layer_bitmap(
                        data, kind, lb, mirror, color, target)
                    self._ui_q.put(("base", token, img, scale, tuple(lb)))
                elif kindjob == "detail":
                    _, _ly, token, data, kind, lb, mirror, color, target = job
                    # 期间又缩放过 -> 直接丢弃 (不清 pending: 那可能已经是新请求的)
                    if _ly.detail_token != token:
                        continue
                    idx = self._layer_index(_ly)
                    img, scale = self._render_layer_bitmap(
                        data, kind, lb, mirror, color, target, clamp=False,
                        index=idx)
                    if _ly.detail_token != token:
                        continue
                    self._ui_q.put(("detail", token, img, scale, tuple(lb)))
                elif kindjob == "diff":
                    _, payload = job
                    res = self._compute_diff(payload)
                    self._ui_q.put(("diff", res))
            except Exception as exc:  # noqa: BLE001
                self._ui_q.put(("status", f"渲染/差异计算出错: {exc}"))

    def _poll(self):
        if self._stop.is_set():
            return
        try:
            while True:
                msg = self._ui_q.get_nowait()
                self._handle_ui(msg)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.win.after(50, self._poll)
        except Exception:
            pass

    def _handle_ui(self, msg):
        t = msg[0]
        if t == "status":
            self.status.set(msg[1])
        elif t == "layer":
            _, data, path, group = msg
            # 批量导入时先不进 Treeview, 等 parse_done 一次性重建 (原来每层都重建整表)
            self._add_layer_data(data, path, group, refresh=False)
        elif t == "parse_err":
            self.status.set(f"解析失败: {msg[1]} — {msg[2]}")
        elif t == "parse_done":
            _, _n, group = msg
            self._batch = max(0, self._batch - _n)
            if self._batch == 0:
                self._refresh_list()                            # 全部解析完成后只重建一次
                notes = self._auto_check_scales(True, True)      # 单位/格式差异自动校正
                if group in ("A", "B") and any(l.group == "A" for l in self.layers) \
                        and any(l.group == "B" for l in self.layers):
                    self._align_group_b_to_a()
                if self._batch_fit:
                    self.fit()
                self.status.set(
                    f"已导入 {len(self.layers)} 层 — 点『开/关』列切换显示 (滚轮缩放/右键平移)")
                if notes:
                    self.info.set("；".join(notes)[:200])
        elif t == "base":
            _, token, img, scale, lb = msg
            for ly in self.layers:
                if ly.token == token and ly.pending:
                    ly.base_img = img
                    ly.base_scale = scale
                    ly.base_bbox = list(lb)
                    ly.pending = False
                    self._lru_clock += 1
                    ly.lru = self._lru_clock
                    break
            self._schedule_render()
        elif t == "detail":
            _, token, img, scale, lb = msg
            for ly in self.layers:
                if ly.detail_token == token and ly.detail_pending:
                    ly.detail_img = img
                    ly.detail_scale = scale
                    ly.detail_bbox = list(lb)
                    ly.detail_pending = False
                    break
            self._schedule_render()
        elif t == "diff":
            self._show_diff(msg[1])

    # ---------------- 图层管理 ----------------
    def _color_for(self, data, group):
        if group == "A":
            c = _WARM[self._group_idx["A"] % len(_WARM)]
            self._group_idx["A"] += 1
            return c
        if group == "B":
            c = _COOL[self._group_idx["B"] % len(_COOL)]
            self._group_idx["B"] += 1
            return c
        try:
            from gerber_layer import identify_layer
            role = identify_layer(data)
        except Exception:  # noqa: BLE001
            role = {"kind": "drill" if data.get("kind") == "drill" else "other",
                    "side": "", "index": None}
        data["_role"] = role
        k = role.get("kind"); side = role.get("side") or ""
        if k == "copper" and side == "inner":
            idx = role.get("index")
            if not idx:
                idx = 1 + sum(1 for l in self.layers
                              if (l.role or {}).get("kind") == "copper"
                              and (l.role or {}).get("side") == "inner")
            return _COPPER_INNER[(idx - 1) % len(_COPPER_INNER)]
        if k == "drill":
            plated = role.get("plated")
            key = "npth" if plated is False else "pth"
            return _TYPE_COLOR.get(("drill", key), "#ffffff")
        return _TYPE_COLOR.get((k, side), _PALETTE[len(self.layers) % len(_PALETTE)])

    def _add_layer_data(self, data, path, group, refresh=True):
        name = os.path.basename(path)
        if group:
            name = f"[{group}] {name}"
        color = self._color_for(data, group)
        ly = GerberLayer(name, data, color, group=group)
        ly.role = data.get("_role") or ly.role
        if data.get("kind") == "drill":
            ly.kind = "drill"
        # 单层导入默认可见; 批量(>1)时保持可见, 由渲染缓存保证流畅
        ly.visible = True
        self.layers.append(ly)
        self._recompute_view_bbox()
        self.selected = len(self.layers) - 1
        if refresh:
            self._refresh_list()
            self._sync_selection()
            self._schedule_render()
        self._request_base(ly)

    def add_layer(self, path, color=None, name=None, fit_now=True):
        data = parse_gerber(path, layer_name=name or os.path.basename(path))
        if color is None:
            color = self._color_for(data, "")
        ly = GerberLayer(name or os.path.basename(path), data, color)
        ly.role = data.get("_role") or {}
        self.layers.append(ly)
        self._recompute_view_bbox()
        self._refresh_list()
        self.selected = len(self.layers) - 1
        self._sync_selection()
        self._request_base(ly)
        if fit_now:
            self.fit()
        else:
            self._schedule_render()
        return ly

    def add_drill(self, path, fit_now=True):
        data = parse_excellon(path)
        color = self._color_for(data, "")
        ly = GerberLayer(os.path.basename(path), data, color)
        ly.kind = "drill"
        ly.role = data.get("_role") or {"kind": "drill"}
        self.layers.append(ly)
        self._recompute_view_bbox()
        self._refresh_list()
        self.selected = len(self.layers) - 1
        self._sync_selection()
        self._request_base(ly)
        if fit_now:
            self.fit()
        else:
            self._schedule_render()
        return ly

    def _enqueue_parse(self, paths, group="", do_fit=True):
        paths = [p for p in (paths or []) if p]
        if not paths:
            return
        self._batch += len(paths)
        self._batch_fit = do_fit
        self._parse_q.put((paths, group))

    def _import_gerber(self):
        paths = filedialog.askopenfilenames(
            title="导入 Gerber 文件 (可多选)",
            filetypes=[("Gerber", "*.gbr *.ger *.art *.pho *.gto *.gbo *.gts *.gbs *.gtl *.gbl"),
                       ("All files", "*.*")])
        self._enqueue_parse(paths)

    def _import_drill(self):
        paths = filedialog.askopenfilenames(title="导入钻孔 (NC Drill, 可多选)",
                                            filetypes=[("Drill", "*.drl *.txt"), ("All files", "*.*")])
        self._enqueue_parse(paths)

    def _import_dir(self):
        d = filedialog.askdirectory(title="导入目录下全部 Gerber/钻孔")
        if not d:
            return
        self._enqueue_parse(self._gather_dir(d))

    def _gather_dir(self, d):
        exts = (".gbr", ".ger", ".art", ".pho", ".gto", ".gbo", ".gts", ".gbs",
                ".gtl", ".gbl", ".gtp", ".gbp", ".gko", ".gm1", ".drl")
        paths = []
        for root_, _dirs, files in os.walk(d):
            for fn in sorted(files):
                if fn.lower().endswith(exts):
                    paths.append(os.path.join(root_, fn))
        return paths

    def _compare_load(self, group):
        paths = filedialog.askopenfilenames(
            title=f"对比 {group}: 选择多层 Gerber/钻孔 (可多选; 取消则改为选目录)",
            filetypes=[("Gerber/Drill", "*.gbr *.ger *.art *.pho *.drl *.gto *.gbo *.gts *.gbs *.gtl *.gbl"),
                       ("All files", "*.*")])
        if not paths:
            d = filedialog.askdirectory(title=f"对比 {group}: 选择包含多层的目录")
            if d:
                paths = self._gather_dir(d)
        if not paths:
            return
        self._enqueue_parse(paths, group=group, do_fit=True)
        self.info.set(f"对比模式: 已载入 {group} 组 {len(paths)} 层 — 载入 A、B 后点『自动定位差异』")

    def _clear(self):
        self.layers = []
        self.selected = None
        self.diff_points = []
        self._group_idx = {"A": 0, "B": 0}
        self._batch = 0
        self._recompute_view_bbox()
        self._refresh_list()
        self._schedule_render()

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, ly in enumerate(self.layers):
            mark = "■" if ly.visible else "□"
            tag = ly.color if ly.visible else "off"
            if ly.visible:
                try:
                    self.tree.tag_configure(ly.color, foreground=ly.color)
                except Exception:
                    pass
            self.tree.insert("", tk.END, iid=str(i),
                             values=(mark,
                                     ly.name + (" ⚠" if (ly.data or {}).get("warnings") else ""),
                                     ly.type_label(), ly.unit_label()),
                             tags=(tag,))
        self._sync_selection()

    def _on_tree_click(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        mods = event.state & 0x0005
        if col == "#1" and not mods:
            i = int(row)
            self._toggle_visible(i)
            return "break"

    def _on_tree_dbl(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        col = self.tree.identify_column(event.x)
        if col == "#1":
            self._toggle_visible(int(row))
        else:
            self.selected = int(row)
            self._sync_selection()
            self._pick_color()

    def _pick_color(self):
        """修改选中图层颜色 (KiCad 风格: 双击图层名或点『改颜色』)"""
        ly = self._sel_layer()
        if ly is None:
            self.info.set("请先在左侧列表中选中一个图层, 再修改颜色")
            return
        try:
            from tkinter import colorchooser
            _rgb, hexc = colorchooser.askcolor(
                color=ly.color or "#ffffff",
                title=f"选择颜色: {ly.name}", parent=self.win)
        except Exception as exc:  # noqa: BLE001
            self.info.set(f"打开取色器失败: {exc}")
            return
        if not hexc:
            return
        ly.color = hexc
        ly.solid = None
        ly.detail_img = None
        self._invalidate(ly)          # 颜色已烘焙进位图 → 需重渲
        self._refresh_list()
        self.info.set(f"{ly.name}: 颜色已改为 {hexc}")

    def _toggle_visible(self, i):
        if not (0 <= i < len(self.layers)):
            return
        ly = self.layers[i]
        ly.visible = not ly.visible
        if ly.visible:
            self._request_base(ly)        # 已缓存且比例相近时直接复用 → 开关瞬时
        self._evict_cache()
        self.selected = i
        self._refresh_list()
        self._schedule_render()

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if sel and sel[0].isdigit():
            self.selected = int(sel[0])
            ly = self._sel_layer()
            if ly is not None:
                self.info.set(ly.detail_text()[:200])
            self._schedule_render()

    def _sync_selection(self):
        try:
            self.tree.selection_remove(*self.tree.selection())
        except Exception:
            pass
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            self.tree.selection_set(str(self.selected))
            self.tree.see(str(self.selected))

    def _selected_indices(self):
        return [int(s) for s in self.tree.selection() if s.isdigit()]

    def _set_sel(self, value):
        for i in self._selected_indices():
            ly = self.layers[i]
            if ly.visible != value:
                ly.visible = value
                if value:
                    self._request_base(ly)
        self._evict_cache()
        self._refresh_list()
        self._schedule_render()

    def _all_on(self):
        for ly in self.layers:
            if not ly.visible:
                ly.visible = True
                self._request_base(ly)
        self._refresh_list()
        self._schedule_render()

    def _all_off(self):
        for ly in self.layers:
            ly.visible = False
        self._evict_cache()
        self._refresh_list()
        self._schedule_render()

    def _invert(self):
        for ly in self.layers:
            ly.visible = not ly.visible
            if ly.visible:
                self._request_base(ly)
        self._evict_cache()
        self._refresh_list()
        self._schedule_render()

    def _img_bytes(self, ly):
        try:
            w, h = ly.base_img.size
            return w * h          # L 掩膜: 1 字节/像素
        except Exception:  # noqa: BLE001
            return 0

    def _evict_cache(self):
        """隐藏层位图按 LRU 保留 (便于瞬时开关), 总内存超预算时释放最久未用的隐藏层"""
        cached = [l for l in self.layers if l.base_img is not None]
        if not cached:
            return
        total = sum(self._img_bytes(l) for l in cached)
        if total <= BUDGET_BYTES:
            return
        hidden = sorted((l for l in cached if not l.visible), key=lambda l: l.lru)
        for l in hidden:
            if total <= BUDGET_BYTES:
                break
            total -= self._img_bytes(l)
            l.base_img = None
            l.solid = None
            l.pending = False

    def _move_layer(self, direction):
        i = self.selected
        if i is None:
            return
        j = i + direction
        if 0 <= j < len(self.layers):
            self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
            self.selected = j
            self._refresh_list()
            self._schedule_render()

    def _delete_sel(self):
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            self.layers.pop(self.selected)
            self.selected = len(self.layers) - 1 if self.layers else None
            self._recompute_view_bbox()
            self._refresh_list()
            self._schedule_render()

    def _sel_layer(self):
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            return self.layers[self.selected]
        return None

    def _center_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.offset = [0.0, 0.0]
            self._recompute_view_bbox()
            self._schedule_render()

    def _reset_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.offset = [0.0, 0.0]
            if ly.mirror_x:
                ly.mirror_x = False
                self._invalidate(ly)
            self._recompute_view_bbox()
            self._schedule_render()

    def _mirror_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.mirror_x = not ly.mirror_x
            self._invalidate(ly)
            self._recompute_view_bbox()
            self._schedule_render()

    def _invalidate(self, ly):
        self._last_img = None
        ly.base_img = None
        ly.solid = None
        ly.detail_img = None
        ly.detail_token += 1
        ly.detail_pending = False
        ly.token += 1
        ly.pending = False
        if ly.visible:
            self._request_base(ly)

    def _ref_layer(self):
        for i, ly in enumerate(self.layers):
            if i != self.selected and ly.visible and ly.bbox():
                return ly
        return None

    def _align_center(self):
        ly = self._sel_layer(); ref = self._ref_layer()
        if not ly or not ref:
            return
        b = ly.bbox(); rb = ref.bbox()
        if not b or not rb:
            return
        ly.offset = [ly.offset[0] + (rb[0] + rb[2]) / 2 - (b[0] + b[2]) / 2,
                     ly.offset[1] + (rb[1] + rb[3]) / 2 - (b[1] + b[3]) / 2]
        self._recompute_view_bbox()
        self._schedule_render()
        self.info.set(f"{ly.name}: 已按中心对齐到 {ref.name}")

    def _align_bl(self):
        ly = self._sel_layer(); ref = self._ref_layer()
        if not ly or not ref:
            return
        b = ly.bbox(); rb = ref.bbox()
        if not b or not rb:
            return
        ly.offset = [ly.offset[0] + rb[0] - b[0], ly.offset[1] + rb[1] - b[1]]
        self._recompute_view_bbox()
        self._schedule_render()
        self.info.set(f"{ly.name}: 已按左下角对齐到 {ref.name}")

    def _align_group_b_to_a(self):
        A = [l for l in self.layers if l.group == "A" and l.local_bbox()]
        B = [l for l in self.layers if l.group == "B" and l.local_bbox()]
        if not A or not B:
            return
        def grp_bbox(ls):
            bs = [l.local_bbox() for l in ls]
            return (min(b[0] for b in bs), min(b[1] for b in bs),
                    max(b[2] for b in bs), max(b[3] for b in bs))
        ab = grp_bbox(A); bb = grp_bbox(B)
        dx = (ab[0] + ab[2]) / 2 - (bb[0] + bb[2]) / 2
        dy = (ab[1] + ab[3]) / 2 - (bb[1] + bb[3]) / 2
        for l in B:
            l.offset = [dx, dy]
        self._recompute_view_bbox()

    # ---------------- 比例校正 (不同 CAM 导出的单位/格式差异) ----------------
    def _layer_span(self, ly):
        b = ly.local_bbox()
        if not b:
            return 0.0
        return max(b[2] - b[0], b[3] - b[1])

    def _group_layers(self, group, drills=None):
        out = []
        for ly in self.layers:
            if (ly.group or "") != (group or ""):
                continue
            if drills is not None and (ly.kind == "drill") != drills:
                continue
            if self._layer_span(ly) > 0:
                out.append(ly)
        return out

    def _ref_span(self, group):
        """参考图幅: 取非钻孔层的中位数 (钻孔层常只覆盖局部区域)"""
        spans = sorted(self._layer_span(l) for l in self._group_layers(group, drills=False))
        if not spans:
            spans = sorted(self._layer_span(l) for l in self._group_layers(group))
        return spans[len(spans) // 2] if spans else 0.0

    def _after_scale(self, layers, msg):
        for ly in layers:
            self._invalidate(ly)
        self._recompute_view_bbox()
        self._refresh_list()
        self._schedule_render()
        if msg:
            self.info.set(msg[:200])

    def _scale_sel(self, factor):
        ly = self._sel_layer()
        if not ly:
            self.info.set("请先在左侧列表中选中一个图层")
            return
        ly.apply_scale(factor)
        self._after_scale([ly], f"{ly.name} 已按比例校正 ×{factor:g} → {ly.detail_text()}")

    def _scale_sel_custom(self):
        from tkinter import simpledialog
        s = simpledialog.askstring("自定义比例",
                                   "输入缩放系数 (例: 25.4 / 0.0393701 / 10 / 0.5):",
                                   parent=self.win)
        if not s:
            return
        try:
            f = float(s.strip())
        except ValueError:
            messagebox.showwarning("比例校正", f"无法解析系数: {s}", parent=self.win)
            return
        if f <= 0:
            messagebox.showwarning("比例校正", "系数必须为正数", parent=self.win)
            return
        self._scale_sel(f)

    def _scale_sel_reset(self):
        ly = self._sel_layer()
        if not ly:
            self.info.set("请先在左侧列表中选中一个图层")
            return
        ly.reset_scale()
        self._after_scale([ly], f"{ly.name} 已恢复原始比例 → {ly.detail_text()}")

    def _scale_b_to_a(self):
        A = self._group_layers("A")
        B = self._group_layers("B")
        if not A or not B:
            self.info.set("需要 A、B 两组都已导入才能做尺寸匹配")
            return
        sa, sb = self._ref_span("A"), self._ref_span("B")
        if sa <= 0 or sb <= 0:
            return
        f = sa / sb
        if abs(f - 1.0) < 1e-4:
            self.info.set(f"A/B 尺寸已一致 ({sa:.2f}mm)")
            return
        for ly in B:
            ly.apply_scale(f)
        self._align_group_b_to_a()
        self._after_scale(B, f"B组已按 A/B 尺寸比等比缩放 ×{f:.4f} (A={sa:.2f}mm, B={sb:.2f}mm)")

    def _auto_check_scales(self, fix=True, report=True):
        """自动检查/校正比例: 钻孔层 vs 线路层, A组 vs B组

        只在比值命中常见单位/格式错误倍数 (10、100、25.4、1/10、1/25.4 ...) 时才自动修正,
        其它情况仅提示, 由用户决定 (拼版、局部钻孔等属正常差异)。
        """
        msgs = []
        changed = []
        for group in ("", "A", "B"):
            drills = self._group_layers(group, drills=True)
            if not drills:
                continue
            ref = self._ref_span(group)
            if ref <= 0:
                continue
            for ly in drills:
                s = self._layer_span(ly)
                if s <= 0:
                    continue
                ratio = ref / s
                f = _match_known_factor(ratio)
                if not f or abs(ratio - 1.0) <= 0.02:
                    continue
                if fix:
                    ly.apply_scale(f, auto=True)
                    changed.append(ly)
                    msgs.append(f"[{ly.name}] 钻孔比例自动校正 ×{f:g}")
                else:
                    msgs.append(f"[{ly.name}] 与线路层相差约 ×{ratio:.3f}")
        A, B = self._group_layers("A"), self._group_layers("B")
        if A and B:
            sa, sb = self._ref_span("A"), self._ref_span("B")
            if sa > 0 and sb > 0:
                ratio = sa / sb
                f = _match_known_factor(ratio)
                if f and abs(ratio - 1.0) > 0.02:
                    if fix:
                        for ly in B:
                            ly.apply_scale(f, auto=True)
                            changed.append(ly)
                        self._align_group_b_to_a()
                        msgs.append(f"B组整体比例自动校正 ×{f:g}")
                    else:
                        msgs.append(f"A/B 组尺寸相差约 ×{ratio:.3f} (可用『B组尺寸匹配A组』)")
                elif abs(ratio - 1.0) > 0.05:
                    msgs.append(f"A/B 尺寸比 {ratio:.3f}, 非标准单位倍数 (可能为拼版/局部差异)")
        if changed:
            self._after_scale(changed, "；".join(msgs))
        elif report and msgs:
            self.info.set("；".join(msgs)[:200])
        return msgs

    # ---------------- 变换 ----------------
    def _bbox_all(self):
        boxes = [ly.bbox() for ly in self.layers if ly.visible and ly.bbox()]
        if not boxes:
            boxes = [ly.bbox() for ly in self.layers if ly.bbox()]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _recompute_view_bbox(self):
        b = self._bbox_all()
        self._view_bbox = b or (0.0, 0.0, 100.0, 100.0)
        return self._view_bbox

    def _scale(self):
        vb = self._view_bbox or self._recompute_view_bbox()
        minx, miny, maxx, maxy = vb
        W = self.canvas.winfo_width() or 800
        H = self.canvas.winfo_height() or 600
        base = min(W / max(maxx - minx, 1e-6), H / max(maxy - miny, 1e-6)) * 0.9
        return base * self.zoom, minx, miny, maxx, maxy, W, H

    def _sc(self):
        return self._frame_scale if self._frame_scale is not None else self._scale()

    def _to_canvas(self, x, y):
        s, minx, miny, maxx, maxy, W, H = self._sc()
        cx = (x - (minx + maxx) / 2) * s + W / 2 + self.pan[0]
        cy = H / 2 - (y - (miny + maxy) / 2) * s + self.pan[1]
        return cx, cy

    def _canvas_to_mm(self, px, py):
        s, minx, miny, maxx, maxy, W, H = self._sc()
        x = (px - W / 2 - self.pan[0]) / s + (minx + maxx) / 2
        y = (maxy + miny) / 2 - (py - H / 2 - self.pan[1]) / s
        return x, y

    def fit(self):
        self._recompute_view_bbox()
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._last_img = None
        for ly in self.layers:
            ly.detail_img = None
            ly.detail_pending = False
            ly.detail_token += 1
            if ly.visible:
                self._request_base(ly)
        self._schedule_render()

    def _on_wheel(self, event):
        """滚轮缩放: 以鼠标所在位置为锚点, 缩放后光标下的图形保持不动"""
        self._zoom_at(1.2 if event.delta > 0 else 1.0 / 1.2, event.x, event.y)

    def _zoom_at(self, factor, px=None, py=None):
        wx = wy = None
        if px is not None and py is not None:
            wx, wy = self._canvas_to_mm(px, py)      # 缩放前光标下的世界坐标
        self._fast_preview = True
        self.zoom = max(0.02, min(self.zoom * factor, 400.0))
        if wx is not None:
            s, minx, miny, maxx, maxy, W, H = self._scale()
            self.pan[0] = px - W / 2 - (wx - (minx + maxx) / 2) * s
            self.pan[1] = py - H / 2 + (wy - (miny + maxy) / 2) * s
        self._interactive = True
        self._schedule_render()
        self._schedule_settle()

    def _zoom(self, factor):
        self._zoom_at(factor)

    def _focus_mm(self, x, y, zoom=None):
        if zoom is not None:
            self.zoom = zoom
        s, minx, miny, maxx, maxy, W, H = self._scale()
        self.pan = [-(x - (minx + maxx) / 2) * s, (y - (miny + maxy) / 2) * s]
        for ly in self.layers:
            if ly.visible:
                self._request_base(ly)
        self._schedule_render()
        self._schedule_settle()

    def _on_canvas_configure(self, _event):
        """窗口尺寸变化: 先立刻重画, 再防抖地重建缓存。

        原来要等 120ms 才画, 期间旧图仍按老尺寸贴在左上角, 看起来就是
        "窗口变大以后四周的图形消失了"。
        """
        self._schedule_render()
        if self._cfg_job is not None:
            try:
                self.win.after_cancel(self._cfg_job)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._cfg_job = self.win.after(250, self._do_resize_render)
        except Exception:  # noqa: BLE001
            self._cfg_job = None

    def _do_resize_render(self):
        self._cfg_job = None
        for ly in self.layers:
            ly.detail_img = None          # 视口变了 -> 高清块作废, 等重新渲染
            ly.detail_pending = False
            ly.detail_token += 1
        self._schedule_settle()

    def _schedule_render(self):
        if self._render_job is not None:
            return
        try:
            self._render_job = self.win.after(16, self._do_render)
        except Exception:
            self._do_render()

    def _do_render(self):
        self._render_job = None
        try:
            self._render()
        except Exception as exc:  # noqa: BLE001
            try:
                self.status.set(f"渲染出错: {exc}")
            except Exception:
                pass
        finally:
            self._frame_scale = None          # 中途抛错也不会把比例尺冻住

    def _schedule_settle(self):
        """交互停止后, 按当前比例请求更清晰的缓存位图"""
        if self._settle_job is not None:
            try:
                self.win.after_cancel(self._settle_job)
            except Exception:
                pass
        try:
            self._settle_job = self.win.after(220, self._do_settle)
        except Exception:
            pass

    def _do_settle(self):
        self._settle_job = None
        self._interactive = False
        self._fast_preview = False
        for ly in self.layers:
            if not self._layer_on(ly):
                continue
            self._request_base(ly)
            self._request_detail(ly)      # 放大时补一块屏幕分辨率的高清图
        self._schedule_render()

    # ---------------- 交互 ----------------
    def _on_press(self, event):
        if self._measure_on:
            self._measure_click(event)
            return
        if not getattr(self, "move_mode", None) or not self.move_mode.get():
            self._pan_start(event)
            return
        self._fast_preview = False          # 拖图层要看到层真的动, 不能用预览
        self._drag = {"mode": "layer", "px": event.x, "py": event.y}
        ly = self._sel_layer()
        if ly:
            self._drag["ox"], self._drag["oy"] = ly.offset

    def _on_motion_measure(self, event):
        if not self._measure_on or not self._measure_pts:
            return
        self._measure_cur = self._canvas_to_mm(event.x, event.y)
        self._refresh_measure()

    def _on_motion(self, event):
        if not self._drag or self._drag["mode"] != "layer":
            return
        ly = self._sel_layer()
        if not ly:
            return
        s = self._scale()[0] or 1.0
        ly.offset[0] = self._drag["ox"] + (event.x - self._drag["px"]) / s
        ly.offset[1] = self._drag["oy"] - (event.y - self._drag["py"]) / s
        self._interactive = True
        self._schedule_render()
        self.info.set(f"{ly.name}: 偏移 X={ly.offset[0]:.3f}mm Y={ly.offset[1]:.3f}mm")

    def _on_release(self, _event):
        if self._drag:
            self._drag = None
            self._recompute_view_bbox()
            self._schedule_settle()

    def _pan_start(self, event):
        self._drag = {"mode": "pan", "px": event.x, "py": event.y,
                      "ox": self.pan[0], "oy": self.pan[1]}

    def _pan_move(self, event):
        if self._drag and self._drag["mode"] == "pan":
            self.pan = [self._drag["ox"] + (event.x - self._drag["px"]),
                        self._drag["oy"] + (event.y - self._drag["py"])]
            self._interactive = True
            self._fast_preview = True
            self._schedule_render()

    def _on_hover(self, event):
        # 鼠标每移动 1px 就刷 Label 会让 Tk 反复重排 -> 60ms 节流
        self._hover_xy = (event.x, event.y)
        if self._hover_job is None:
            try:
                self._hover_job = self.win.after(60, self._flush_hover)
            except Exception:  # noqa: BLE001
                self._hover_job = None

    def _flush_hover(self):
        self._hover_job = None
        if not self._hover_xy:
            return
        x, y = self._canvas_to_mm(*self._hover_xy)
        self.status.set(f"X={x:.3f}mm  Y={y:.3f}mm   (mil: {x*39.3701:.1f} x {y*39.3701:.1f})")

    # ---------------- 位图缓存请求 ----------------
    def _viewport_world(self, pad=0.12):
        """当前视口对应的世界坐标范围 (四周外扩一点, 小距离平移不必立刻重渲)"""
        s = self._scale()[0]
        if not s or s <= 0:
            return None
        x0, y0 = self._canvas_to_mm(0, 0)
        x1, y1 = self._canvas_to_mm(self.canvas.winfo_width(), self.canvas.winfo_height())
        lox, hix = min(x0, x1), max(x0, x1)
        loy, hiy = min(y0, y1), max(y0, y1)
        pw = (hix - lox) * pad
        ph = (hiy - loy) * pad
        return (lox - pw, loy - ph, hix + pw, hiy + ph)

    def _layer_index(self, ly):
        """按需建空间索引 (图层大才建; 重建过的比例校正会使它失效)"""
        data = ly.data or {}
        store = data.get("primitives")
        if store is None or getattr(store, "n", 0) < 20000:
            return None                      # 图元少, 线性扫更快
        sig = (store.n, round(ly.scale_factor or 1.0, 6))
        if ly.index is not None and ly.index_sig == sig:
            return ly.index
        lb = ly.local_bbox()
        if not lb:
            return None
        try:
            ly.index = _GridIndex(store, data, lb)
            ly.index_sig = sig
        except Exception:                    # noqa: BLE001
            ly.index = None
            return None
        return ly.index

    def _request_detail(self, ly):
        """按当前视口 + 屏幕分辨率渲染一块高清位图 (只在放大超过缓存能力时才做)"""
        if not self._layer_on(ly) or not ly.data or ly.base_img is None \
                or ly.base_scale <= 0:
            return
        s = self._scale()[0]
        if not s or s <= ly.base_scale * 1.12:
            return                          # 缓存已经够清晰
        vw = self._viewport_world()
        lb = ly.local_bbox()
        if not vw or not lb:
            return
        ox, oy = ly.offset
        x0 = max(vw[0] - ox, lb[0]); x1 = min(vw[2] - ox, lb[2])
        y0 = max(vw[1] - oy, lb[1]); y1 = min(vw[3] - oy, lb[3])
        if x1 - x0 <= 1e-9 or y1 - y0 <= 1e-9:
            return                          # 该层不在视口内
        bb = ly.detail_bbox
        if (bb and ly.detail_scale > 0
                and abs(ly.detail_scale - s) / max(s, 1e-9) < 0.06
                and x0 >= bb[0] and y0 >= bb[1] and x1 <= bb[2] and y1 <= bb[3]):
            return                          # 现有高清块仍然覆盖
        if ly.detail_pending:
            return
        ly.detail_token += 1
        ly.detail_pending = True
        self._render_q.put(("detail", ly, ly.detail_token, ly.data, ly.kind,
                            [x0, y0, x1, y1], ly.mirror_x, ly.color, s))

    def _request_base(self, ly):
        lb = ly.local_bbox()
        if not lb:
            return
        w = max(lb[2] - lb[0], 1e-6); h = max(lb[3] - lb[1], 1e-6)
        s = self._scale()[0] or 1.0
        # 自适应分辨率上限: 可见/已缓存层越多, 单层缓存越小, 总内存有界
        vis = max(1, sum(1 for l in self.layers if l.visible or l.base_img is not None))
        # L 掩膜 1 字节/像素, 每层可用像素数 = 预算/层数 -> 层多时分辨率也不至于塌掉
        cap = int(math.sqrt(max(1.0, BUDGET_BYTES / vis)))
        cap = max(1200, min(MAX_BASE, cap))
        hard_cap = cap / max(w, h)          # 全层位图的物理分辨率上限 (px/mm)
        target = min(s * QUALITY, hard_cap)
        if ly.pending:
            return
        if ly.base_img is not None and ly.base_scale > 0:
            # 已经到上限: 放大时靠 detail 块补清晰度, 不再每次缩放都重光栅化整层
            if ly.base_scale >= hard_cap * 0.98:
                return
            if abs(ly.base_scale - target) / max(target, 1e-6) < 0.55:
                return
        ly.token += 1
        ly.pending = True
        self._render_q.put(("base", ly.token, ly.data, ly.kind, list(lb),
                            ly.mirror_x, ly.color, target))

    # ---------------- 纯渲染 (后台线程安全, 不触碰 Tk) ----------------
    def _ap_shape_data(self, data, ap_code, width):
        aps = data.get("apertures") or {}
        ap = aps.get(ap_code)
        scale = 25.4 if data.get("unit") == "inch" else 1.0
        # 无光圈定义 或 自定义宏光圈(解析时已展开, 图元自带 mm 直径)
        if not ap or ap.get("macro"):
            w = width or 0.2
            return ("C", w, w, 0, 0.0)
        shape = str(ap.get("shape", "C"))
        params = [x for x in (ap.get("params") or []) if isinstance(x, (int, float))]
        if shape == "C" or not params:
            w = (params[0] if params else (width or 0.2) / max(scale, 1e-9)) * scale
            return ("C", w, w, 0, 0.0)
        if shape == "R":
            return ("R", params[0] * scale,
                    (params[1] if len(params) > 1 else params[0]) * scale, 0, 0.0)
        if shape == "O":
            return ("O", params[0] * scale,
                    (params[1] if len(params) > 1 else params[0]) * scale, 0, 0.0)
        if shape == "P":
            d = params[0] * scale
            edges = int(params[1]) if len(params) > 1 else 5
            rot = params[2] if len(params) > 2 else 0.0
            return ("P", d, d, edges, rot)
        return ("C", params[0] * scale if params else 0.2, 0, 0, 0.0)

    @staticmethod
    def _arc_segs(r_px):
        """按屏幕半径决定圆弧段数 (固定 16 段在深放大时会变成可见折线)"""
        r = max(abs(r_px), 0.3)
        c = 1.0 - 0.3 / r                      # 弦高 <= 0.3px
        if c <= -1.0:
            return 6
        try:
            seg = int(math.pi / max(math.acos(c), 1e-6))
        except (ValueError, ZeroDivisionError):
            return 16
        return max(6, min(512, seg))

    def _arc_points(self, x1, y1, x2, y2, i, j, cw, seg=None, scale=1.0):
        cx, cy = x1 + i, y1 + j
        r = math.hypot(x1 - cx, y1 - cy)
        if seg is None:
            seg = self._arc_segs(r * (scale or 1.0))
        a1 = math.atan2(y1 - cy, x1 - cx)
        a2 = math.atan2(y2 - cy, x2 - cx)
        if cw:
            while a2 > a1:
                a2 -= 2 * math.pi
        else:
            while a2 < a1:
                a2 += 2 * math.pi
        n = int(seg)
        d = (a2 - a1) / n
        return [(cx + r * math.cos(a1 + d * k), cy + r * math.sin(a1 + d * k))
                for k in range(n + 1)]

    def _draw_prims(self, draw, data, kind, T, scale, fill, clear_fill,
                    ap_cache=None, clip=None, indices=None):
        """把一层图元画进 draw。

        clip=(x0,y0,x1,y1): 跳过完全在视口外的图元 (深放大时 95% 在屏幕外)
        indices: 只画这些图元序号 (空间索引给的候选), 传入时不再遍历全层
        """
        store = data.get("primitives")
        if store is not None and getattr(store, "n", 0):
            rec = store.rec
            extras = store.extras
            rng = range(store.n) if indices is None else indices
            cnt = 0
            for i in rng:
                cnt += 1
                if not cnt & 1023:
                    time.sleep(0)          # 定期让出 GIL: 后台重渲时界面不僵
                b = i * 9
                code = int(rec[b])
                if code == 4:                              # 解析错误占位
                    continue
                c = clear_fill if (code & 8) else fill
                t = code & 7
                x1 = rec[b + 1]; y1 = rec[b + 2]
                x2 = rec[b + 3]; y2 = rec[b + 4]
                wd = rec[b + 5]
                ap = int(rec[b + 6])
                if t == 1:                                 # flash
                    if ap_cache is not None and ap in ap_cache:
                        shape, fw, fh, edges, rot = ap_cache[ap]
                    else:
                        shape, fw, fh, edges, rot = self._ap_shape_data(data, ap, wd)
                        if ap_cache is not None:
                            ap_cache[ap] = (shape, fw, fh, edges, rot)
                    cx, cy = T(x1, y1)
                    if clip is not None:
                        m = max(fw, fh) / 2 * scale + 2
                        if (cx + m < clip[0] or cx - m > clip[2]
                                or cy + m < clip[1] or cy - m > clip[3]):
                            continue
                    if shape == "R":
                        hw, hh = fw / 2 * scale, fh / 2 * scale
                        draw.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], fill=c)
                    elif shape == "O":
                        hw, hh = fw / 2 * scale, fh / 2 * scale
                        rr = min(hw, hh)
                        if fw >= fh:
                            draw.ellipse([cx - hw, cy - hh, cx - hw + 2 * rr, cy + hh], fill=c)
                            draw.ellipse([cx + hw - 2 * rr, cy - hh, cx + hw, cy + hh], fill=c)
                            draw.rectangle([cx - hw + rr, cy - hh, cx + hw - rr, cy + hh], fill=c)
                        else:
                            draw.ellipse([cx - hw, cy - hh, cx + hw, cy - hh + 2 * rr], fill=c)
                            draw.ellipse([cx - hw, cy + hh - 2 * rr, cx + hw, cy + hh], fill=c)
                            draw.rectangle([cx - hw, cy - hh + rr, cx + hw, cy + hh - rr], fill=c)
                    elif shape == "P" and edges >= 3:
                        r = fw / 2 * scale
                        pts = [(cx + r * math.cos(math.radians(rot + k * 360.0 / edges)),
                                cy + r * math.sin(math.radians(rot + k * 360.0 / edges)))
                               for k in range(edges)]
                        draw.polygon(pts, fill=c)
                    else:
                        r = max(0.6, fw / 2 * scale)
                        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
                elif t == 2:                               # arc
                    pts = [T(px, py) for px, py in self._arc_points(
                        x1, y1, x2, y2, rec[b + 7], rec[b + 8],
                        bool(code & 16), scale=scale)]
                    if len(pts) >= 2:
                        wpx = max(1, int(round(wd * scale)))
                        if clip is not None and _outside(pts, clip, wpx + 2):
                            continue
                        draw.line(pts, fill=c, width=wpx, joint="curve")
                elif t == 3:                               # region
                    e = extras.get(i)
                    pl = e[0] if e else None
                    if pl:
                        pts = [T(px, py) for px, py in pl]
                        if len(pts) >= 3:
                            if clip is not None and _outside(pts, clip, 1):
                                continue
                            draw.polygon(pts, fill=c)
                else:                                      # draw
                    wpx = max(1, int(round(wd * scale)))
                    a = T(x1, y1)
                    bb = T(x2, y2)
                    if clip is not None and (
                            max(a[0], bb[0]) + wpx < clip[0]
                            or min(a[0], bb[0]) - wpx > clip[2]
                            or max(a[1], bb[1]) + wpx < clip[1]
                            or min(a[1], bb[1]) - wpx > clip[3]):
                        continue
                    draw.line([a, bb], fill=c, width=wpx)
        if kind == "drill":
            for hd in (data.get("holes") or []):
                if hd.get("x") is None:
                    continue
                cx, cy = T(hd["x"], hd["y"])
                dia = hd.get("diameter_mm") or 0.3
                r = max(0.7, dia / 2 * scale)
                if clip is not None and (cx + r < clip[0] or cx - r > clip[2]
                                         or cy + r < clip[1] or cy - r > clip[3]):
                    continue
                if hd.get("is_slot") and hd.get("x2") is not None and hd.get("y2") is not None:
                    ex, ey = T(hd["x2"], hd["y2"])
                    draw.line([cx, cy], [ex, ey], fill=fill, width=max(1, int(round(r * 2))))
                    draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=fill)
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)

    @staticmethod
    def _make_xform(bbox, scale, img_h, mirror, offset):
        minx, miny = bbox[0], bbox[1]
        ox, oy = offset

        def T(x, y):
            lx = -x if mirror else x
            return ((lx + ox - minx) * scale, img_h - ((y + oy - miny) * scale))
        return T

    def _render_layer_bitmap(self, data, kind, lb, mirror, color, target_scale,
                             clamp=True, index=None, indices=None):
        """渲染单层掩膜 (L 灰度): 255 = 该处有图形, 0 = 空白。

        抗锯齿: PIL 的 ImageDraw 是硬边, 位图不大时按 2x 超采样绘制再 LANCZOS 降采样,
        边缘才有过渡像素; 位图很大时降回 1x, 避免内存/耗时失控。
        """
        Image, ImageDraw, _ = self._PIL()
        minx, miny, maxx, maxy = lb
        w = max(maxx - minx, 1e-6); h = max(maxy - miny, 1e-6)
        scale = target_scale
        if clamp:                           # detail 块只覆盖视口, 不受全层尺寸上限约束
            scale = min(scale, MAX_BASE / max(w, h))
        scale = max(scale, 1e-3)
        W = max(1, int(round(w * scale)) + 1)
        H = max(1, int(round(h * scale)) + 1)
        ss = 2 if W * H <= SS_MAX_PIXELS else 1
        dw, dh = W * ss, H * ss
        img = Image.new("L", (dw, dh), 0)
        draw = ImageDraw.Draw(img)
        T = self._make_xform(lb, scale * ss, dh, mirror, (0.0, 0.0))
        # detail 块的位图就是视口本身 -> 屏幕外的图元可以直接跳过
        clip = None if clamp else (0.0, 0.0, float(dw), float(dh))
        sel = indices
        if sel is None and index is not None:
            sel = index.query(lb[0], lb[1], lb[2], lb[3])
        self._draw_prims(draw, data, kind, T, scale * ss, 255, 0,
                         ap_cache={}, clip=clip, indices=sel)
        if ss > 1:
            img = img.resize((W, H), Image.LANCZOS)
        return img, scale

    def _render_mask(self, specs, bbox, scale):
        Image, ImageDraw, _ = self._PIL()
        minx, miny, maxx, maxy = bbox
        w = max(maxx - minx, 1e-6); h = max(maxy - miny, 1e-6)
        W = max(1, int(round(w * scale)) + 1)
        H = max(1, int(round(h * scale)) + 1)
        img = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(img)
        for (data, kind, mirror, offset) in specs:
            T = self._make_xform(bbox, scale, H, mirror, offset)
            self._draw_prims(draw, data, kind, T, scale, 255, 0)
        return img, (W, H)

    # ---------------- 合成显示 ----------------
    def _render(self):
        cv = self.canvas
        W = cv.winfo_width(); H = cv.winfo_height()
        if W < 4 or H < 4:
            return
        Image, ImageDraw, ImageTk = self._PIL()
        self._frame_scale = self._scale()
        sel = self._sel_layer()
        any_pending = False
        # 缩放/平移期间直接变换上一帧整图 (一次重采样, ~20ms);
        # 逐层合成要 170~290ms/帧, 那是滚轮卡顿的根源。
        canvas_img = None
        if self._interactive and self._fast_preview:
            canvas_img = self._preview_canvas(W, H)
        if canvas_img is None:
            canvas_img = Image.new("RGBA", (W, H), BG_RGB + (255,))
            for ly in self.layers:
                if not self._layer_on(ly):
                    continue
                if ly.base_img is None:
                    if not ly.pending:
                        self._request_base(ly)
                    any_pending = True
                    self._draw_placeholder(canvas_img, ly)
                    continue
                self._composite_layer(canvas_img, ly, W, H)
                self._lru_clock += 1
                ly.lru = self._lru_clock
            if not self._interactive:
                # 快照 (不含选中框/差异标记), 供交互期做预览
                self._last_img = canvas_img.copy()
                self._last_view = self._canvas_world_rect(W, H)
        # 网络高亮: 交互预览帧里跳过 (预览图本身已含高亮, 不必每帧重算)
        if self._hl and not (self._interactive and self._fast_preview):
            self._draw_highlight(canvas_img, W, H)
        d = ImageDraw.Draw(canvas_img)
        if sel is not None and self._layer_on(sel):
            self._draw_sel_rect(d, sel)
        if self.diff_points:
            self._draw_diff_markers(d)
        if self._photo is None or self._photo.width() != W or self._photo.height() != H:
            self._photo = ImageTk.PhotoImage(canvas_img)
            if self._img_item is None:
                self._img_item = cv.create_image(0, 0, image=self._photo, anchor="nw")
            else:
                cv.itemconfig(self._img_item, image=self._photo)
                cv.coords(self._img_item, 0, 0)
        else:
            try:
                self._photo.paste(canvas_img)     # 复用同一 Tk 图像, 省掉每帧新建/整幅拷贝
            except Exception:                     # noqa: BLE001
                self._photo = ImageTk.PhotoImage(canvas_img)
                cv.itemconfig(self._img_item, image=self._photo)
        self._frame_scale = None
        self._evict_cache()
        self._refresh_measure()
        self._frame_img = canvas_img      # 供调试/回归取最终帧 (含高亮)
        if any_pending:
            self.status.set("渲染中 ... (后台)")

    def _canvas_world_rect(self, W, H):
        """画布 (0,0)-(W,H) 对应的世界坐标范围"""
        x0, y0 = self._canvas_to_mm(0, 0)
        x1, y1 = self._canvas_to_mm(W, H)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _preview_canvas(self, W, H):
        """把上一帧整图按新的缩放/平移变换成当前画面 (单次重采样)"""
        img = self._last_img
        vw = self._last_view
        if img is None or not vw:
            return None
        Image = self._PIL()[0]
        # 上一帧覆盖的世界矩形, 在当前视口里落在哪
        X0, Y0 = self._to_canvas(vw[0], vw[3])
        X1, Y1 = self._to_canvas(vw[2], vw[1])
        sw = X1 - X0; sh = Y1 - Y0
        if sw <= 0.5 or sh <= 0.5:
            return None
        # 只取落在画布内的那部分, 避免深放大时把整图放大到几十万像素
        fx0 = max(0.0, -X0) / sw; fx1 = min(1.0, (W - X0) / sw)
        fy0 = max(0.0, -Y0) / sh; fy1 = min(1.0, (H - Y0) / sh)
        if fx1 - fx0 <= 0.002 or fy1 - fy0 <= 0.002:
            return None
        W0, H0 = img.size
        sx0 = int(fx0 * W0); sx1 = int(fx1 * W0)
        sy0 = int(fy0 * H0); sy1 = int(fy1 * H0)
        if sx1 - sx0 < 1 or sy1 - sy0 < 1:
            return None
        src = img.crop((sx0, sy0, sx1, sy1))
        dw = max(1, int(round((fx1 - fx0) * sw)))
        dh = max(1, int(round((fy1 - fy0) * sh)))
        if src.size != (dw, dh):
            src = src.resize((dw, dh), Image.BILINEAR)
        dx = int(round(X0 + fx0 * sw)); dy = int(round(Y0 + fy0 * sh))
        canvas_img = Image.new("RGBA", (W, H), BG_RGB + (255,))
        canvas_img.paste(src, (dx, dy))
        return canvas_img

    def _blit(self, canvas_img, ly, img, bbox, W, H, color=None):
        """把一张掩膜按当前视口贴到画布上 (自动裁剪; 上采样用 BICUBIC 保持锐利)

        color 可覆盖本层颜色 (网络高亮用)。
        """
        if img is None or not bbox:
            return
        ox, oy = ly.offset
        X0, Y0 = self._to_canvas(bbox[0] + ox, bbox[3] + oy)
        X1, Y1 = self._to_canvas(bbox[2] + ox, bbox[1] + oy)
        if X1 - X0 < 0.5 or Y1 - Y0 < 0.5:
            return
        cx0 = max(0, int(math.floor(X0))); cy0 = max(0, int(math.floor(Y0)))
        cx1 = min(W, int(math.ceil(X1)));  cy1 = min(H, int(math.ceil(Y1)))
        dw = cx1 - cx0; dh = cy1 - cy0
        if dw < 1 or dh < 1:
            return
        bw, bh = img.size
        bx0 = max(0, min(bw - 1, int((cx0 - X0) / (X1 - X0) * bw)))
        bx1 = max(bx0 + 1, min(bw, int((cx1 - X0) / (X1 - X0) * bw)))
        by0 = max(0, min(bh - 1, int((cy0 - Y0) / (Y1 - Y0) * bh)))
        by1 = max(by0 + 1, min(bh, int((cy1 - Y0) / (Y1 - Y0) * bh)))
        mask = img.crop((bx0, by0, bx1, by1))
        if mask.size != (dw, dh):
            Image = self._PIL()[0]
            if mask.size[0] < dw or mask.size[1] < dh:
                # 交互中 BILINEAR 保帧率, 停止后 BICUBIC 更锐
                mask = mask.resize(
                    (dw, dh), Image.BILINEAR if self._interactive else Image.BICUBIC)
            else:
                mask = mask.resize((dw, dh), Image.BOX)   # 缩小用 BOX, 不产生摩尔纹
        # 直接按颜色贴图: 源图尺寸再小也不会因为裁切越界而补出黑块
        rgb = color if isinstance(color, tuple) else _hex_rgb(color or ly.color)
        canvas_img.paste(rgb, (cx0, cy0, cx1, cy1), mask)

    def _detail_usable(self, ly):
        """高清块是否与当前视口匹配 (比例一致且覆盖了视口与图层的交集)"""
        d = ly.detail_img
        if d is None or not ly.detail_bbox or ly.detail_scale <= 0:
            return False
        s = self._sc()[0]
        if not s or s <= 0:
            return False
        if abs(ly.detail_scale - s) / max(s, 1e-9) >= 0.06:
            return False                       # 缩放变了
        vw = self._viewport_world(pad=0.0)
        lb = ly.bbox()                         # 显示坐标 (含偏移)
        if not vw or not lb:
            return False
        ox, oy = ly.offset
        need = (max(vw[0], lb[0]) - ox, max(vw[1], lb[1]) - oy,
                min(vw[2], lb[2]) - ox, min(vw[3], lb[3]) - oy)
        if need[2] <= need[0] or need[3] <= need[1]:
            return False
        bb = ly.detail_bbox                    # 平移过 -> 覆盖不到就回落粗缓存
        return (need[0] >= bb[0] - 1e-6 and need[1] >= bb[1] - 1e-6
                and need[2] <= bb[2] + 1e-6 and need[3] <= bb[3] + 1e-6)

    def _composite_layer(self, canvas_img, ly, W, H):
        # 高清块有效时直接用它, 不再叠粗缓存 (被放大的粗图会把清晰边缘糊掉)
        if self._detail_usable(ly):
            self._blit(canvas_img, ly, ly.detail_img, ly.detail_bbox, W, H)
        else:
            self._blit(canvas_img, ly, ly.base_img, ly.base_bbox or ly.local_bbox(), W, H)

    def _draw_placeholder(self, canvas_img, ly):
        b = ly.bbox()
        if not b:
            return
        Image, ImageDraw, _ = self._PIL()
        d = ImageDraw.Draw(canvas_img)
        x0, y0 = self._to_canvas(b[0], b[3])
        x1, y1 = self._to_canvas(b[2], b[1])
        d.rectangle([x0, y0, x1, y1], outline=_hex_rgb(ly.color) + (140,), width=2)

    # ---------------- Solo / 网络高亮 / 测距 ----------------
    def _layer_on(self, ly):
        """该图层当前是否参与绘制 (Solo 生效时只显示那一层)"""
        if self._solo is not None:
            return ly is self._solo
        return ly.visible

    def _solo_toggle(self):
        if self._solo is not None:
            self._solo = None
            self.status.set("已退出单层隔离")
        else:
            ly = self._sel_layer()
            if ly is None:
                self.status.set("先选中一个图层再按 Solo")
                return
            self._solo = ly
            self.status.set(f"单层隔离: {ly.name} (再按一次 Solo 退出)")
        self._last_img = None
        self._schedule_render()

    def _net_map(self, ly):
        """net -> [图元序号]; 只在第一次点选时构建"""
        if getattr(ly, "net_map", None) is not None:
            return ly.net_map
        m = {}
        store = (ly.data or {}).get("primitives")
        if store is not None:
            ex = store.extras
            for i in range(store.n):
                e = ex.get(i)
                if e and e[1]:
                    m.setdefault(e[1], []).append(i)
        ly.net_map = m
        return m

    def _prim_at(self, ly, lx, lyy):
        """本地坐标命中的图元序号 (取最上面一个)"""
        store = (ly.data or {}).get("primitives")
        if store is None or not getattr(store, "n", 0):
            return None
        tol = 4.0 / max(self._sc()[0], 1e-6)
        rec = store.rec
        extras = store.extras
        idx = getattr(ly, "index", None)
        cand = idx.query(lx - tol, lyy - tol, lx + tol, lyy + tol) if idx else None
        hit = None
        for i in (range(store.n) if cand is None else cand):
            b = i * 9
            if int(rec[b]) == 4:
                continue
            bb = _prim_bbox(rec, b, extras, i, {})
            if bb and bb[0] <= lx <= bb[2] and bb[1] <= lyy <= bb[3]:
                hit = i                     # 后面的在上层
        return hit

    def _highlight_at(self, event):
        """双击: 高亮该点所在图元的整条网络"""
        x, y = self._canvas_to_mm(event.x, event.y)
        fallback = None                     # 最上面的命中 (无网络名时兜底)
        for ly in reversed(self.layers):
            if not self._layer_on(ly):
                continue
            ox, oy = ly.offset
            lx = x - ox
            if ly.mirror_x:
                lx = -lx
            i = self._prim_at(ly, lx, y - oy)
            if i is None:
                continue
            store = ly.data["primitives"]
            e = store.extras.get(i)
            net = (e[1] if e else "") or ""
            if net:
                idxs = self._net_map(ly).get(net) or [i]
                self._hl = {"net": net, "items": [(ly, idxs)]}
                ref = e[2] if e else ""
                pin = e[3] if e else ""
                tip = f"{ref}.{pin} " if (ref and pin) else ""
                self.status.set(f"网络高亮: {net}  {tip}({len(idxs)} 个图元, Esc 清除)")
            else:
                if fallback is None:
                    fallback = (ly, i)
                continue                  # 继续往下找带网络名的图元
            self._last_img = None
            self._schedule_render()
            return
        if fallback is not None:
            ly, i = fallback
            self._hl = {"net": "", "items": [(ly, [i])]}
            self.status.set("该处没有网络名 (X1 文件无 %TO.N), 只高亮这个图元; Esc 清除")
            self._last_img = None
            self._schedule_render()
            return
        self.status.set("光标下没有图形")

    def _draw_highlight(self, canvas_img, W, H):
        hl = self._hl
        if not hl:
            return
        s = self._sc()[0]
        vw = self._viewport_world(pad=0.0)
        for ly, idxs in hl["items"]:
            if not self._layer_on(ly) or not idxs:
                continue
            lb = ly.local_bbox()
            if not lb or not vw:
                continue
            ox, oy = ly.offset
            x0 = max(vw[0] - ox, lb[0]); x1 = min(vw[2] - ox, lb[2])
            y0 = max(vw[1] - oy, lb[1]); y1 = min(vw[3] - oy, lb[3])
            if x1 - x0 <= 1e-9 or y1 - y0 <= 1e-9:
                continue
            try:
                mask, _sc = self._render_layer_bitmap(
                    ly.data, ly.kind, [x0, y0, x1, y1], ly.mirror_x, ly.color, s,
                    clamp=False, indices=idxs)
            except Exception:            # noqa: BLE001
                continue
            self._blit(canvas_img, ly, mask, [x0, y0, x1, y1], W, H, color=HL_RGB)

    def _clear_overlays(self, _e=None):
        self._hl = None
        self._measure_on = False
        if getattr(self, "_measure_var", None) is not None:
            self._measure_var.set(False)
        self._measure_pts = []
        self._measure_cur = None
        self._last_img = None
        self._refresh_measure()
        self._schedule_render()
        self.status.set("已清除高亮/测距")

    # ---------------- 测距 / 测角度 (画在 Tk 画布上, 不重绘位图) ----------------
    def _measure_toggle(self):
        self._measure_on = not self._measure_on
        self._measure_var.set(self._measure_on)
        self._measure_pts = []
        self._measure_cur = None
        self._refresh_measure()
        if self._measure_on:
            self.status.set("测距: 左键点第 1 点, 第 2 点得到距离/角度, 第 3 点测夹角; Esc 退出")
        else:
            self.status.set("测距已关闭")

    def _measure_click(self, event):
        if len(self._measure_pts) >= 3:
            self._measure_pts = []
        self._measure_pts.append(self._canvas_to_mm(event.x, event.y))
        self._measure_cur = None
        self._refresh_measure()

    def _refresh_measure(self):
        """用 Tk 画布对象画测距线和读数 (避免每次鼠标移动都重绘整幅位图)"""
        cv = getattr(self, "canvas", None)
        if cv is None or not hasattr(cv, "delete"):
            return
        try:
            cv.delete("measure")
            if not self._measure_on and not self._measure_pts:
                return
            pts = [self._to_canvas(x, y) for x, y in self._measure_pts]
            if self._measure_on and self._measure_cur and pts:
                pts.append(self._to_canvas(*self._measure_cur))
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                cv.create_line(ax, ay, bx, by, fill="#00e5ff", width=2, tags="measure")
            for (ax, ay) in pts:
                cv.create_oval(ax - 3, ay - 3, ax + 3, ay + 3,
                               outline="#00e5ff", width=2, tags="measure")
            txt = self._measure_text()
            if txt:
                ax, ay = pts[-1]
                cv.create_text(ax + 12, ay + 12, text=txt, anchor="nw", tags="measure",
                               fill="#00e5ff", font=("Consolas", 10, "bold"))
        except Exception:                # noqa: BLE001
            pass

    def _measure_text(self):
        p = self._measure_pts
        if len(p) < 2:
            return ""
        (x1, y1), (x2, y2) = p[0], p[1]
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        ang = math.degrees(math.atan2(dy, dx))
        s = f"{dist:.3f} mm   dx={dx:+.3f}  dy={dy:+.3f}   {ang:.2f}deg"
        if len(p) >= 3:
            (x3, y3) = p[2]
            v1 = (x1 - x2, y1 - y2)
            v2 = (x3 - x2, y3 - y2)
            n1 = math.hypot(*v1)
            n2 = math.hypot(*v2)
            if n1 > 1e-9 and n2 > 1e-9:
                cosv = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
                s += f"\n夹角={math.degrees(math.acos(cosv)):.2f}deg"
        return s

    def _draw_sel_rect(self, d, ly):
        b = ly.bbox()
        if not b:
            return
        x0, y0 = self._to_canvas(b[0], b[3])
        x1, y1 = self._to_canvas(b[2], b[1])
        d.rectangle([x0, y0, x1, y1], outline=(255, 152, 0, 255), width=2)

    def _draw_diff_markers(self, d):
        s = self._sc()[0]
        for dp in self.diff_points[:MAX_MARKERS]:
            x, y = self._to_canvas(dp["x"], dp["y"])
            col = (255, 0, 170, 235) if dp["side"] == "A" else (0, 220, 255, 235)
            ext = dp.get("ext", 0.0)
            r = max(4.0, min(40.0, ext * s * 0.6))
            if r > 8:
                x0, y0 = self._to_canvas(dp["bx0"], dp["by1"])
                x1, y1 = self._to_canvas(dp["bx1"], dp["by0"])
                d.rectangle([x0, y0, x1, y1], outline=col, width=2)
            d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=2)
            d.line([x - r, y, x + r, y], fill=col, width=1)
            d.line([x, y - r, x, y + r], fill=col, width=1)

    # ---------------- 自动差异定位 ----------------
    def _auto_diff(self):
        A = [l for l in self.layers if l.group == "A" and l.visible and l.local_bbox()]
        B = [l for l in self.layers if l.group == "B" and l.visible and l.local_bbox()]
        if not A or not B:
            messagebox.showinfo(
                "自动定位差异",
                "请先用『对比A: 导入多层』『对比B: 导入多层』分别载入 A、B 两组图层。\n"
                "普通导入的图层不参与 A/B 对比。")
            return
        boxes = [l.bbox() for l in (A + B)]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
        w = max(bbox[2] - bbox[0], 1e-6); h = max(bbox[3] - bbox[1], 1e-6)
        scale = min(DIFF_PX / max(w, h), 200.0)
        specA = [(l.data, l.kind, l.mirror_x, tuple(l.offset)) for l in A]
        specB = [(l.data, l.kind, l.mirror_x, tuple(l.offset)) for l in B]
        payload = {"bbox": bbox, "scale": scale, "A": specA, "B": specB,
                   "namesA": [l.name for l in A], "namesB": [l.name for l in B]}
        self.status.set("正在计算 A/B 差异 ...")
        self._render_q.put(("diff", payload))

    def _compute_diff(self, payload):
        Image, _, _ = self._PIL()
        bbox = payload["bbox"]; scale = payload["scale"]
        mA, size = self._render_mask(payload["A"], bbox, scale)
        mB, _ = self._render_mask(payload["B"], bbox, scale)
        gw = gh = DIFF_GRID
        ga = mA.resize((gw, gh), Image.BOX)
        gb = mB.resize((gw, gh), Image.BOX)
        da = ga.load(); db = gb.load()
        minx, miny, maxx, maxy = bbox
        cw = (maxx - minx) / gw; chh = (maxy - miny) / gh
        cell_mm2 = cw * chh

        # 每格差异侧: 'A' / 'B' / None
        side_grid = [[None] * gw for _ in range(gh)]
        diffv_grid = [[0.0] * gw for _ in range(gh)]
        na_cells = nb_cells = 0
        for gy in range(gh):
            row_s = side_grid[gy]; row_d = diffv_grid[gy]
            for gx in range(gw):
                a = da[gx, gy] / 255.0
                b = db[gx, gy] / 255.0
                dv = a - b
                if dv >= DIFF_THRESH:
                    row_s[gx] = "A"; row_d[gx] = dv; na_cells += 1
                elif -dv >= DIFF_THRESH:
                    row_s[gx] = "B"; row_d[gx] = -dv; nb_cells += 1

        # 连通域聚合 (4-邻接, 同侧) → 每个差异簇一条记录
        clusters = []
        seen = [[False] * gw for _ in range(gh)]
        stack = []
        for gy in range(gh):
            for gx in range(gw):
                sd = side_grid[gy][gx]
                if sd is None or seen[gy][gx]:
                    continue
                seen[gy][gx] = True
                stack.append((gx, gy))
                cells = 0; wsum = 0.0; sx = 0.0; sy = 0.0
                bx0 = by0 = 1e18; bx1 = by1 = -1e18
                while stack:
                    cx, cy = stack.pop()
                    dv = diffv_grid[cy][cx]
                    cells += 1; wsum += dv
                    mmx = minx + (cx + 0.5) * cw
                    mmy = maxy - (cy + 0.5) * chh
                    sx += mmx * dv; sy += mmy * dv
                    if cx < bx0: bx0 = cx
                    if cx > bx1: bx1 = cx
                    if cy < by0: by0 = cy
                    if cy > by1: by1 = cy
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < gw and 0 <= ny < gh and not seen[ny][nx] and side_grid[ny][nx] == sd:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                if wsum <= 0:
                    wsum = 1.0
                ext_mm = max((bx1 - bx0 + 1) * cw, (by1 - by0 + 1) * chh)
                clusters.append({
                    "x": sx / wsum, "y": sy / wsum, "side": sd,
                    "area": round(wsum * cell_mm2, 4),
                    "cells": cells, "ext": round(ext_mm, 4),
                    "bx0": minx + bx0 * cw, "bx1": minx + (bx1 + 1) * cw,
                    "by0": maxy - (by1 + 1) * chh, "by1": maxy - by0 * chh,
                })
        clusters.sort(key=lambda c: c["area"], reverse=True)
        na = sum(1 for c in clusters if c["side"] == "A")
        nb = sum(1 for c in clusters if c["side"] == "B")
        return {"points": clusters[:MAX_MARKERS], "total": len(clusters),
                "na": na, "nb": nb, "na_cells": na_cells, "nb_cells": nb_cells,
                "namesA": payload["namesA"], "namesB": payload["namesB"],
                "cell_mm": (round(cw, 4), round(chh, 4))}

    def _show_diff(self, res):
        self.diff_points = res["points"]
        self._schedule_render()
        na, nb, total = res["na"], res["nb"], res["total"]
        cw, chh = res["cell_mm"]
        self.status.set(f"差异定位完成: {total} 处 (A多 {na} / B多 {nb})  网格 {cw}×{chh}mm  "
                        f"[A层 {len(res['namesA'])} vs B层 {len(res['namesB'])}]")
        if total == 0:
            messagebox.showinfo("自动定位差异", "未发现明显差异 (A/B 在容差内一致)。")
            return
        self._open_diff_window(res)

    def _open_diff_window(self, res):
        win = tk.Toplevel(self.win)
        win.title(win_title("A/B 差异定位结果"))
        from win_dpi import apply_window
        apply_window(win, 760, 520)
        head = ttk.Frame(win, padding=6)
        head.pack(fill=tk.X)
        ttk.Label(head, text=(f"A 组 {len(res['namesA'])} 层 vs B 组 {len(res['namesB'])} 层    "
                              f"差异 {res['total']} 处 (A多 {res['na']} / B多 {res['nb']})"),
                  font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(head, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

        cols = ("i", "side", "x", "y", "area", "cells")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=20)
        heads = {"i": "#", "side": "差异侧", "x": "X(mm)", "y": "Y(mm)",
                 "area": "差异面积mm²", "cells": "网格数"}
        widths = {"i": 50, "side": 76, "x": 104, "y": 104, "area": 130, "cells": 80}
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=widths[c], anchor=tk.CENTER)
        tree.tag_configure("A", foreground="#c0008a")
        tree.tag_configure("B", foreground="#0080a0")
        vsb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=6)
        for i, dp in enumerate(res["points"], 1):
            tree.insert("", tk.END, iid=str(i - 1), values=(
                i, "A 多" if dp["side"] == "A" else "B 多",
                f"{dp['x']:.3f}", f"{dp['y']:.3f}",
                dp["area"], dp["cells"]), tags=(dp["side"],))

        def focus(_e=None):
            s = tree.selection()
            if not s:
                return
            dp = res["points"][int(s[0])]
            self._focus_mm(dp["x"], dp["y"], zoom=min(self.zoom * 2.2, 200.0))
            self.info.set(f"已定位差异: ({dp['x']:.3f}, {dp['y']:.3f}) {'A多' if dp['side']=='A' else 'B多'}")

        tree.bind("<Double-Button-1>", focus)
        foot = ttk.Frame(win, padding=6)
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(foot, text="定位选中(双击亦可)", command=focus).pack(side=tk.LEFT)
        ttk.Label(foot, text="提示: 双击任一行可在版图中放大定位到该差异位置",
                  foreground="#555").pack(side=tk.LEFT, padx=8)

    # ---------------- 导出 ----------------
    def _export_png(self):
        try:
            from PIL import Image
        except Exception:
            messagebox.showerror("导出失败", "未安装 Pillow")
            return
        bbox = self._bbox_all()
        if not bbox:
            messagebox.showinfo("导出", "没有可导出的图层")
            return
        path = filedialog.asksaveasfilename(title="导出 PNG", defaultextension=".png",
                                            initialfile="gerber_view.png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        vis = [l for l in self.layers if l.visible and l.local_bbox()]
        if not vis:
            vis = [l for l in self.layers if l.local_bbox()]
        W, H = 2000, 1400
        w = max(bbox[2] - bbox[0], 1e-6); h = max(bbox[3] - bbox[1], 1e-6)
        scale = min(W / w, H / h) * 0.94
        Image, ImageDraw, _ = self._PIL()
        ow = max(1, int(w * scale)); oh = max(1, int(h * scale))
        img = Image.new("RGBA", (ow, oh), BG_RGB + (255,))
        draw = ImageDraw.Draw(img)
        for ly in vis:
            T = self._make_xform(bbox, scale, oh, ly.mirror_x, tuple(ly.offset))
            self._draw_prims(draw, ly.data, ly.kind, T, scale,
                             _hex_rgb(ly.color) + (235,), BG_RGB + (255,))
        img.convert("RGB").save(path)
        messagebox.showinfo("导出完成", path)
