# -*- coding: utf-8 -*-
"""端到端回归: 真实 Tk 窗口 + 渲染 + 高亮 + Solo + 测距 + 性能数字"""
import io
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk

from gerber_reader import parse_excellon, parse_gerber, scale_fab_data
import gerber_viewer as gv

# GitHub Actions 的 Windows runner 上，控制台默认不是 UTF-8（cp1252），
# 直接 print 中文会抛 UnicodeEncodeError 让整个回归误判为失败；
# 这里把标准输出/错误流强制为 UTF-8，任何平台都能稳定打印。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_FAILS = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name
          + (("  " + detail) if detail and not cond else ""))
    if not cond:
        _FAILS.append(name)


def test_parsers(tmp):
    """无界面回归: 比例校正光圈 / Excellon 模态坐标、内联槽孔、R# 重复"""
    def write(name, text):
        p = os.path.join(tmp, name)
        io.open(p, "w", encoding="ascii").write(text)
        return p

    def gerber_with_ap(ap):
        return ("%FSLAX34Y34*%\n%MOMM*%\n%ADD10" + ap
                + "*%\nD10*\nX10000Y10000D03*\nM02*\n")

    # 英寸光圈缩放: 0.040in = 1.016mm, ×2 后应为 2.032mm
    p = write("_rg_inch.gbr", "%FSLAX24Y24*%\n%MOIN*%\n%ADD10C,0.040*%\n"
                             "D10*\nX0100Y0100D03*\nM02*\n")
    d = scale_fab_data(parse_gerber(p), 2.0)
    val = (d.get("apertures") or {}).get(10, {}).get("params", [None])[0]
    check("inch aperture scale", val is not None and abs(val - 2.032) < 1e-6,
          "params=%r" % (val,))

    # 多边形 P: 边数/旋转角不随比例缩放
    p = write("_rg_poly.gbr", gerber_with_ap("P,2.000X6X30"))
    d = scale_fab_data(parse_gerber(p), 2.0)
    params = (d.get("apertures") or {}).get(10, {}).get("params")
    check("polygon aperture scale", params == [4.0, 6.0, 30.0], "params=%r" % (params,))

    # Excellon: Y-only 模态坐标
    p = write("_rg_yonly.drl", "M48\nMETRIC,TZ\nT1C0.30\n%\nT1\n"
                                "X010000Y010000\nY020000\nY030000\nM30\n")
    r = parse_excellon(p)
    ys = sorted(h["y"] for h in r["holes"])
    check("excellon Y-only", r["hole_count"] == 3 and ys == [10.0, 20.0, 30.0],
          "holes=%r" % ([(h["x"], h["y"]) for h in r["holes"]],))

    # Excellon: 内联 G85 槽孔
    p = write("_rg_slot.drl", "M48\nMETRIC,TZ\nT1C0.30\n%\nT1\n"
                               "X010000Y010000G85X020000Y010000\nM30\n")
    r = parse_excellon(p)
    h = r["holes"][0] if r["holes"] else {}
    check("excellon inline G85", h.get("is_slot") and h.get("x2") == 20.0,
          "hole=%r" % (h,))

    # Excellon: R# 按当前位移重复
    p = write("_rg_repeat.drl", "M48\nMETRIC,TZ\nT1C0.30\n%\nT1\n"
                                 "X010000Y010000\nX020000Y010000R3\nM30\n")
    r = parse_excellon(p)
    xs = [h["x"] for h in r["holes"]]
    check("excellon R# repeat", xs == [10.0, 20.0, 30.0, 40.0], "xs=%r" % (xs,))

    # Excellon: R# 单独成行
    p = write("_rg_repeat_line.drl", "M48\nMETRIC,TZ\nT1C0.30\n%\nT1\n"
                                      "X010000Y010000\nX020000Y010000\nR3\nM30\n")
    r = parse_excellon(p)
    xs = [h["x"] for h in r["holes"]]
    check("excellon R# on own line", xs == [10.0, 20.0, 30.0, 40.0], "xs=%r" % (xs,))

    # Excellon: G91 增量坐标
    p = write("_rg_inc.drl", "M48\nMETRIC,TZ\nT1C0.30\n%\nT1\nG91\n"
                              "X010000Y010000\nX010000Y000000\n"
                              "X000000Y010000\nX010000Y010000\nM30\n")
    r = parse_excellon(p)
    pts = [(h["x"], h["y"]) for h in r["holes"]]
    check("excellon G91 incremental",
          pts == [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (30.0, 30.0)],
          "pts=%r" % (pts,))

    # Gerber 宏 code 22: X,Y 为左下角 (不是中心)
    p = write("_rg_m22.gbr", "%FSLAX34Y34*%\n%MOMM*%\n"
                              "%AMLL*22,1,2.0,1.0,1.0,1.0,0*%\n%ADD10LL*%\n"
                              "D10*\nX50000Y50000D03*\nM02*\n")
    d = parse_gerber(p)
    pts = (d["primitives"].extras.get(0) or (None,))[0]
    check("macro code 22 lower-left",
          pts == [(6.0, 6.0), (8.0, 6.0), (8.0, 7.0), (6.0, 7.0)], "pts=%r" % (pts,))

    # Gerber 宏 code 6: 十字线宽=a[6]、线长=a[7] (此前错用 a[7]/a[8])
    p = write("_rg_m6.gbr", "%FSLAX34Y34*%\n%MOMM*%\n"
                             "%AMTG*6,1.0,1.0,4.0,0.5,0.5,2,0.3,1.0,0*%\n%ADD10TG*%\n"
                             "D10*\nX50000Y50000D03*\nM02*\n")
    d = parse_gerber(p)
    st = d["primitives"]
    draws = [(round(st.rec[i * 9 + 5], 3),
              round(st.rec[i * 9 + 1], 3), round(st.rec[i * 9 + 2], 3),
              round(st.rec[i * 9 + 3], 3), round(st.rec[i * 9 + 4], 3))
             for i in range(st.n) if int(st.rec[i * 9]) & 7 == 0]
    check("macro code 6 crosshair",
          len(draws) == 2 and all(x[0] == 0.3 for x in draws)
          and any(abs(x[4] - x[2]) == 1.0 for x in draws), "draws=%r" % (draws,))

    # Gerber 宏跟随 %MI / %IR (此前只平移, 内部几何未镜像/旋转)
    body = ("%FSLAX34Y34*%\n%MOMM*%\n%AMLINE*20,1,$1,0,0,$2,$3,0*%\n"
            "%ADD10LINE,0.3X2.0X0.0*%\n@@TR@@D10*\nX100000Y100000D03*\nM02*\n")
    p = write("_rg_mmi.gbr", body.replace("@@TR@@", "%MIA1B0*%\n"))
    st = parse_gerber(p)["primitives"]
    d0 = [(round(st.rec[i * 9 + 1], 3), round(st.rec[i * 9 + 3], 3))
          for i in range(st.n) if int(st.rec[i * 9]) & 7 == 0]
    check("macro follows %MI", d0 == [(-10.0, -12.0)], "x=%r" % (d0,))


def make_nets(path):
    L = ['%FSLAX34Y34*%', '%MOMM*%', '%ADD10C,0.200*%', '%ADD11C,2.000*%',
         '%ADD12C,0.800*%', 'D10*']
    for i in range(60):
        y = 2 + i * 0.5
        L += ['X%dY%dD02*' % (10000, int(y * 10000)),
              'X%dY%dD01*' % (int(58 * 10000), int(y * 10000))]
    L += ['%TO.N,GND*%', 'D11*']
    for j in range(8):
        L.append('X%dY%dD03*' % (int((5 + j * 6) * 10000), int(10 * 10000)))
    L.append('%TD*%')
    L += ['%TO.N,VCC*%', 'D12*']
    for j in range(8):
        L.append('X%dY%dD03*' % (int((8 + j * 6) * 10000), int(25 * 10000)))
    L.append('%TD*%')
    L += ['D10*']
    for j in range(8):
        x = 5 + j * 6
        L += ['%TO.N,GND*%', 'X%dY%dD02*' % (int(x * 10000), int(10 * 10000)),
              'X%dY%dD01*' % (int(x * 10000), int(20 * 10000)), '%TD*%']
    L.append('M02*')
    io.open(path, 'w', encoding='ascii').write('\n'.join(L))


def test_layer_controls(app, root):
    """真实 Treeview 命中测试: 单击色块/开关, A/B 入口在窄窗口不被裁掉。"""
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    buttons = [w for w in descendants(app.win) if w.winfo_class() == "TButton"]
    check("toolbar color button removed", all(w.cget("text") != "改颜色" for w in buttons))
    check("layer color column exists", app.tree.heading("#0")["text"] == "颜色")
    names = {w.cget("text"): w for w in buttons}
    controls = ("A组: 导入文件", "A组: 导入文件夹", "B组: 导入文件", "B组: 导入文件夹",
                "自动定位差异", "查看差异结果", "导出差异报告", "取消对比")
    for width in (820, 1000, 1220):
        app.win.geometry(f"{width}x780")
        app._show_panel("compare")
        root.update()
        check(f"A/B panel visible at width {width}", all(
            text in names and names[text].winfo_ismapped()
            and names[text].winfo_rootx() >= app.win.winfo_rootx()
            and names[text].winfo_rootx() + names[text].winfo_width()
            <= app.win.winfo_rootx() + app.win.winfo_width()
            for text in controls))
        app._show_panel("layers")
        root.update()
        check(f"canvas has most of the height at width {width}",
              app.canvas.winfo_height() >= app.win.winfo_height() * .78)
        check(f"canvas has most of the width at width {width}",
              app.canvas.winfo_width() >= app.win.winfo_width() * .55)

    app.win.geometry("820x560")
    app._show_panel("compare")
    root.update()
    check("compare actions fit minimum window height", all(
          names[text].winfo_rooty() + names[text].winfo_height()
          <= app.compare_panel.winfo_rooty() + app.compare_panel.winfo_height()
          for text in controls))
    app._show_panel("layers")
    app.win.geometry("1220x780")
    root.update()

    layer = app.layers[0]
    app.selected = 0
    app._refresh_list()
    root.update()
    x, y, w, h = app.tree.bbox("0", "#0")
    event = type("E", (), {"x": x + w // 2, "y": y + h // 2, "state": 0})()
    visible = layer.visible
    with patch("tkinter.colorchooser.askcolor", return_value=((18, 52, 86), "#123456")) as chooser:
        check("single color click consumes event", app._on_tree_click(event) == "break")
        check("single color click changes correct layer", layer.color == "#123456"
              and layer.visible == visible and chooser.call_count == 1)
        app._on_tree_dbl(event)
        check("color double click does not reopen chooser", chooser.call_count == 1)

    root.update()
    x, y, w, h = app.tree.bbox("0", "st")
    event.x, event.y = x + w // 2, y + h // 2
    with patch("tkinter.colorchooser.askcolor") as chooser:
        app._on_tree_click(event)
        check("visibility click does not open color dialog", layer.visible != visible and not chooser.called)
        app._on_tree_dbl(event)
        check("visibility double click does not toggle twice", layer.visible != visible)
    root.update()
    x, y, w, h = app.tree.bbox("0", "#0")
    event.x, event.y = x + w // 2, y + h // 2
    with patch("tkinter.colorchooser.askcolor", return_value=(None, None)) as chooser:
        app._on_tree_click(event)
        check("hidden layer color chooser and cancel", chooser.called
              and layer.color == "#123456" and layer.visible != visible)
    layer.visible = visible
    # 正常解析信息不显示感叹号, 真正警告仍保留。
    original = layer.data
    layer.data = dict(original, warnings=[], notes=["已处理镜像"])
    app._refresh_list()
    check("information does not mark layer as warning", "⚠" not in app.tree.item("0", "values")[1])
    layer.data = dict(original, warnings=["未支持的命令"])
    app._refresh_list()
    check("real warning remains visible", "⚠" in app.tree.item("0", "values")[1])
    layer.data = original
    app._refresh_list()

    with patch.object(app, "_enqueue_parse") as enqueue, \
            patch.object(gv.filedialog, "askopenfilenames", return_value=("top.gtl",)), \
            patch.object(gv.filedialog, "askdirectory", return_value="board") as directory, \
            patch.object(app, "_gather_dir", return_value=["bottom.gbl"]):
        names["A组: 导入文件"].invoke()
        check("A file import uses A group", enqueue.call_args.args[0] == ("top.gtl",)
              and enqueue.call_args.kwargs["group"] == "A" and not directory.called)
        names["B组: 导入文件夹"].invoke()
        check("B folder import uses B group", enqueue.call_args.args[0] == ["bottom.gbl"]
              and enqueue.call_args.kwargs["group"] == "B")
    with patch.object(gv.filedialog, "askopenfilenames", return_value=()), \
            patch.object(gv.filedialog, "askdirectory") as directory:
        names["A组: 导入文件"].invoke()
        check("cancel comparison import does not open another dialog", not directory.called)
    groups = [ly.group for ly in app.layers]
    app.layers[0].group = "A"
    app.layers[1].group = "B"
    app._refresh_list()

    check("browse mode does not move layers by default", not app.move_mode.get())
    check("selected layer bounds hidden by default", not app._bounds_var.get())
    offset = list(app.layers[0].offset)
    original_pan = list(app.pan)
    app._on_press(SimpleNamespace(x=100, y=100))
    app._on_motion(SimpleNamespace(x=120, y=110))
    check("left drag pans in browse mode", app.pan == [original_pan[0] + 20, original_pan[1] + 10]
          and app.layers[0].offset == offset)
    app._on_release(None)
    app.pan = original_pan
    app.status.set("对比结果保留")
    app._hover_xy = (50, 50)
    app._flush_hover()
    check("hover coordinates do not overwrite operation messages", app.status.get() == "对比结果保留"
          and "X " in app._coordinates.get())
    app.status.set("第一行\n第二行")
    check("status bar remains a single line", app._status_display.get() == "第一行")
    height = app.canvas.winfo_height()
    app.info.set("较长提示文字，" * 100)
    root.update()
    check("long hints do not shrink canvas", app.canvas.winfo_height() == height)
    app.info.set("滚轮缩放 · 拖动平移 · 双击高亮网络")
    check("unit and size retained in layer details", "mm" in app.layer_details.get("1.0", tk.END)
          and "图幅" in app.layer_details.get("1.0", tk.END))
    app.move_mode.set(True)
    app._move_mode_toggle()
    app._measure_toggle()
    check("measurement disables layer movement", app._measure_on and not app.move_mode.get())
    app.move_mode.set(True)
    app._move_mode_toggle()
    check("layer movement disables measurement", not app._measure_on and not app._measure_var.get())
    app.move_mode.set(False)
    app._move_mode_toggle()
    check("A/B panel reports imported layer counts", "A组 1 层" in app.compare_summary.get()
          and "B组 1 层" in app.compare_summary.get())
    for ly, group in zip(app.layers, groups):
        ly.group = group
    app._refresh_list()


def test_compare_workflow(app, root, tmp):
    """按钮 -> 异步差异计算 -> 结果列表 -> 再次查看, 使用小图层避免性能夹具干扰。"""
    a = os.path.join(tmp, "a.gtl")
    b = os.path.join(tmp, "b.gtl")
    for path, x in ((a, 100000), (b, 120000)):
        io.open(path, "w", encoding="ascii").write(
            f"%FSLAX34Y34*%%MOMM*%%ADD10C,1.0*%D10*X{x}Y100000D03*M02*")
    app._clear()
    app._enqueue_parse([a], group="A")
    app._enqueue_parse([b], group="B")
    deadline = time.monotonic() + 10
    while app._batch and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    check("A/B asynchronous imports finish with correct groups", app._batch == 0
          and [ly.group for ly in app.layers] == ["A", "B"])
    if app._batch or len(app.layers) != 2:
        return
    with patch("tkinter.simpledialog.askfloat", side_effect=[.05, 0.0]):
        app._auto_diff()
    deadline = time.monotonic() + 10
    while app.diff_result is None and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    check("A/B background computation returns differences", app.diff_result is not None
          and app.diff_result["total"] > 0)
    windows = [w for w in app.win.winfo_children()
               if isinstance(w, tk.Toplevel) and "差异定位结果" in w.title()]
    check("A/B result interface opens", bool(windows))
    for w in windows:
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        root.update()
        trees = [child for child in descendants(w) if child.winfo_class() == "Treeview"]
        check("A/B result interface lists difference rows", bool(trees) and bool(trees[0].get_children()))
        buttons = [child for child in descendants(w) if child.winfo_class() == "TButton"]
        check("A/B result action buttons visible", all(child.winfo_ismapped() and child.winfo_width() > 1
              for child in buttons))
        if trees and trees[0].get_children():
            trees[0].selection_set(trees[0].get_children()[0])
            with patch.object(app, "_focus_mm") as focus:
                next(child for child in buttons if child.cget("text").startswith("定位选中")).invoke()
                check("A/B selected difference locates on canvas", focus.called)
        w.destroy()
    if app.diff_result is not None:
        app._view_diff_results()
    windows = [w for w in app.win.winfo_children() if isinstance(w, tk.Toplevel)]
    check("A/B results can be reopened", bool(windows))
    for w in windows:
        w.destroy()
    app._clear()


def test_measure_workflow(app, root, tmp):
    """Actual canvas picking, preview and legacy parameter reparse."""
    path = os.path.join(tmp, "measure.gtl")
    io.open(path, "w", encoding="ascii").write(
        "%FSLAX34Y34*%%MOMM*%%ADD10C,2*%D10*X00000Y00000D03*X50000Y00000D03*M02*")
    app._clear()
    app.add_layer(path)
    app.fit()
    root.update()
    app._measure_var.set(True)
    app._measure_toggle()
    for mode, expected in (("中心到中心", 5.), ("边到边", 3.), ("中心到边", 4.)):
        app._measure_mode.set(mode)
        app._measure_mode_changed()
        events = [SimpleNamespace(x=app._to_canvas(x, 0)[0], y=app._to_canvas(x, 0)[1]) for x in (0, 5)]
        app._measure_click(events[0])
        app._on_motion_measure(events[1])
        check("measurement snap preview " + mode, f"{expected:.4f} mm" in app._measure_text())
        check("measurement overlay drawn " + mode, bool(app.canvas.find_withtag("measure")))
        app._measure_click(events[1])
        check("measurement final value " + mode, f"{expected:.4f} mm" in app._measure_text())
        check("measurement full readout stays in sidebar " + mode,
              f"{expected:.4f} mm" in app._measure_readout.get() and "dx=" in app._measure_readout.get())
        before = list(app._measure_pts)
        app._on_motion_measure(SimpleNamespace(x=20, y=20))
        check("completed measurement does not add hover clutter " + mode,
              app._measure_pts == before and app._measure_hover is None)
        app._on_motion_measure(events[1])
        items = app.canvas.find_withtag("measure_label")
        check("measurement badge only shows distance " + mode, any(
              app.canvas.type(item) == "text" and app.canvas.itemcget(item, "text") == f"{expected:.4f} mm" for item in items))
    app._clear_overlays()

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    for width in (1000, 1220):
        app.win.geometry(f"{width}x780")
        root.update()
        app._show_panel("measure")
        root.update()
        combo = app.measure_modes
        check("measurement mode visible width " + str(width), combo.winfo_ismapped() and combo.winfo_rootx() + combo.winfo_width() <= app.win.winfo_rootx() + app.win.winfo_width())

    old = os.path.join(tmp, "legacy.gtl")
    io.open(old, "w", encoding="ascii").write("G71*D10*X010000Y020000D03*M02*")
    app._clear()
    app.add_layer(old)
    ly = app.layers[-1]
    check("legacy missing aperture flagged", ly.data.get("missing_apertures") == [10])
    app._legacy_parameters()
    win = next(w for w in app.win.winfo_children() if isinstance(w, tk.Toplevel))
    combos = [w for w in descendants(win) if w.winfo_class() == "TCombobox"]
    combos[0].set("mm"); combos[1].set("2.4"); combos[2].set("L")
    text = next(w for w in descendants(win) if w.winfo_class() == "Text")
    text.insert("1.0", "D10 C 1\n")
    next(w for w in descendants(win) if w.winfo_class() == "TButton" and w.cget("text") == "应用并重新解析").invoke()
    deadline = time.monotonic() + 10
    while app._batch and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    check("legacy reparse replaces existing layer", app._batch == 0 and len(app.layers) == 1 and app.layers[0] is ly)
    check("legacy coordinates and aperture repaired", ly.data["bbox"] == [1., 2., 1., 2.] and not ly.data.get("missing_apertures"))
    check("legacy options retained for project", getattr(ly, "parse_options", {}).get("format_override") == (2, 4))
    app._clear()


def test_role_override(app, root, tmp):
    """手动纠正图层类型: 对话框设置 -> 工程保存 -> 恢复自动识别。"""
    from tkinter import ttk
    import gerber_project as gp

    def widgets(w, cls):
        out = []
        for c in w.winfo_children():
            if isinstance(c, cls):
                out.append(c)
            out.extend(widgets(c, cls))
        return out

    def all_tops(w):
        out = []
        for c in w.winfo_children():
            if isinstance(c, tk.Toplevel):
                out.append(c)
            out.extend(all_tops(c))
        return out

    def open_dialog():
        app._set_layer_role()
        root.update()
        return next(t for t in all_tops(root) if "设置图层类型" in t.title())

    app._clear()
    p = os.path.join(tmp, "cm")
    io.open(p, "w", encoding="ascii").write(
        "%FSLAX26Y26*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nX100000Y100000D03*\nM02*\n")
    app.add_layer(p)
    root.update()
    ly = app.layers[0]
    check("short name cm identified as top copper",
          (ly.role.get("kind"), ly.role.get("side")) == ("copper", "top"))
    app.selected = 0

    dlg = open_dialog()
    cbs = widgets(dlg, ttk.Combobox)
    check("role dialog has kind and side selectors", len(cbs) == 2)
    cbs[0].set("copper｜铜层")
    cbs[1].set("bottom｜底层")
    next(b for b in widgets(dlg, ttk.Button) if b.cget("text") == "确定").invoke()
    root.update()
    check("manual override changes side to bottom", ly.role.get("side") == "bottom")
    check("manual override recorded for project",
          ly.role_override == {"kind": "copper", "side": "bottom", "index": None}
          and ly.role.get("source") == "manual")

    proj = os.path.join(tmp, "role.egv.json")
    gp.save(proj, app.layers)
    rec = gp.load(proj)[0]
    check("role override survives project roundtrip",
          rec["role_override"] == {"kind": "copper", "side": "bottom", "index": None})

    dlg = open_dialog()
    next(b for b in widgets(dlg, ttk.Button) if b.cget("text") == "恢复自动识别").invoke()
    root.update()
    check("restore auto identification clears override",
          ly.role_override is None and ly.role.get("side") == "top")
    app._clear()


def main():
    # 用独立的临时目录，跑完就删掉。不用 gettempdir() 直接落文件：
    # 在"低完整性"环境里（例如本工作区被打了 Low Mandatory Label）%TEMP% 不可写，
    # Python 会把临时目录退化成当前目录，夹具就会掉在仓库根目录里。
    tmp = tempfile.mkdtemp(prefix='egv_test_')
    test_parsers(tmp)

    fp1 = os.path.join(tmp, '_rg_net.gbr')
    fp2 = os.path.join(tmp, '_rg_plane.gbr')
    make_nets(fp1)

    # 第二层: 大面积底铜 (走线密), 用于压力测试
    L = ['%FSLAX34Y34*%', '%MOMM*%', '%ADD10C,0.150*%', 'D10*']
    for i in range(20000):
        x = (i % 400) * 0.15
        y = (i // 400) * 1.0
        L += ['X%dY%dD02*' % (int(x * 10000), int(y * 10000)),
              'X%dY%dD01*' % (int((x + 0.12) * 10000), int(y * 10000))]
    L.append('M02*')
    io.open(fp2, 'w', encoding='ascii').write('\n'.join(L))

    root = tk.Tk()
    root.geometry("1200x820")
    app = gv.GerberViewer(root)
    root.update()

    t = time.time()
    app.add_layer(fp1, name="net")
    app.add_layer(fp2, name="plane")
    root.update()
    print("add_layer %.0f ms" % ((time.time() - t) * 1000))
    print("layers", [ly.name for ly in app.layers])
    test_layer_controls(app, root)

    app.fit()
    for _ in range(3):
        app._interactive = False
        app._fast_preview = False
        app._do_render()
        root.update()
    img = getattr(app, "_frame_img", None) or app._last_img
    print("full frame", None if img is None else img.size)
    if img is None:
        print("[FAIL] 没有渲染结果")
        return 1

    def ink(im):
        px = list(im.convert("RGBA").getdata())
        bg = gv.BG_RGB
        n = sum(1 for p in px if (p[0], p[1], p[2]) != bg)
        return n / float(len(px))

    print("ink ratio %.3f" % ink(img))

    # ---- 回归: 纯平移预览必须逐像素搬运 (不重采样、不糊) ----
    app.pan = [0.0, 0.0]
    app.zoom = 1.0
    app._interactive = False
    app._fast_preview = False
    app._do_render()
    root.update()
    snap = app._last_img.copy()
    W, H = snap.size
    app.pan = [13.0, -9.0]
    app._frame_scale = app._scale()
    pv = app._preview_canvas(W, H)
    app._frame_scale = None
    Image2 = app._PIL()[0]
    exp = Image2.new("RGBA", (W, H), gv.BG_RGB + (255,))
    exp.paste(snap.crop((0, 9, W, H)), (13, 0))
    check("pan preview is pixel shift", pv is not None
          and pv.tobytes() == exp.tobytes())

    # ---- 回归: 拖层快速合成 == 逐层全量合成 ----
    app.pan = [0.0, 0.0]
    app._interactive = False
    app._fast_preview = False
    app._do_render()
    root.update()
    ly = app.layers[0]
    app.selected = 0
    app._drag = {"mode": "layer", "px": 0.0, "py": 0.0, "ox": 0.0, "oy": 0.0}
    app._drag_ly = ly
    app._drag_bg = None
    app._drag_above = None
    app._drag_bg_key = None
    ly.offset[0] += 0.4
    ly.offset[1] -= 0.2
    app._frame_scale = app._scale()
    got = app._drag_canvas(W, H)
    want = Image2.new("RGBA", (W, H), gv.BG_RGB + (255,))
    for _l in app.layers:
        if _l.visible and _l.base_img is not None:
            app._composite_layer(want, _l, W, H)
    app._frame_scale = None
    from PIL import ImageChops
    worst = max(ImageChops.difference(got, want).getextrema(),
                key=lambda t: t[1])[1] if got is not None else 255
    check("drag fast composite matches full",
          got is not None and worst <= 1, "max channel diff=%r" % (worst,))
    ly.offset = [0.0, 0.0]
    app._drag = None
    app._drag_ly = None
    app._drag_bg = None
    app._drag_above = None
    app._drag_bg_key = None
    app._do_render()
    root.update()

    # ---- 网络高亮 ----
    ly = app.layers[0]
    ev = type("E", (), {})()
    app.layers[1].visible = False     # 关掉顶层的无网络底铜, 避免挡住焊盘
    ev.x, ev.y = app._to_canvas(5.0 + ly.offset[0], 10.0 + ly.offset[1])
    app._highlight_at(ev)
    print("hl net:", None if not app._hl else app._hl["net"],
          "prims:", 0 if not app._hl else len(app._hl["items"][0][1]))
    for _ in range(2):
        app._interactive = False
        app._fast_preview = False
        app._do_render()
        root.update()
    img2 = getattr(app, "_frame_img", None) or app._last_img
    hl = sum(1 for p in img2.convert("RGBA").getdata()
             if abs(p[0] - 255) < 25 and abs(p[1] - 214) < 25 and p[2] < 60)
    print("highlight pixels", hl)

    # ---- Solo ----
    app.selected = 0
    app._solo_toggle()
    app._interactive = False
    app._fast_preview = False
    app._do_render()
    root.update()
    print("solo on:", app._solo is ly, "ink %.3f" % ink(app._frame_img))
    app._solo_toggle()
    print("solo off:", app._solo is None)

    # ---- 测距 ----
    app._measure_pts = [(0.0, 0.0), (3.0, 4.0), (3.0, 0.0)]
    txt = app._measure_text().replace("\n", " | ")
    print("measure:", txt)
    app._measure_pts = []
    app._clear_overlays()

    # ---- 深放大: 高清块 + 索引 ----
    app.layers[1].visible = True
    app.zoom = 30.0
    app.pan[0] = 0
    app.pan[1] = 0
    app._interactive = False
    app._fast_preview = False
    t = time.time()
    app._do_render()
    root.update()
    print("deep zoom render %.0f ms" % ((time.time() - t) * 1000))
    app._do_settle()
    t = time.time()
    for _ in range(120):
        root.update()
        time.sleep(0.03)
        if all(ly.detail_img is not None for ly in app.layers if ly.visible):
            break
    print("detail ready %.0f ms" % ((time.time() - t) * 1000))
    prints = []
    for _ly in app.layers:
        prints.append((_ly.name, None if _ly.index is None else len(_ly.index.items)))
    print("index items", prints)
    print("detail imgs", [(ly.name, None if ly.detail_img is None else ly.detail_img.size)
                          for ly in app.layers])

    # ---- 回归: 删除/清空后 Solo 引用残留 ----
    app._clear()
    app.add_layer(fp1, name="solo")
    app.selected = 0
    app._solo_toggle()
    app._delete_sel()
    check("solo cleared on delete", app._solo is None)
    app._clear()
    app.add_layer(fp1, name="after")
    check("clear resets solo", app._solo is None
          and all(app._layer_on(ly) for ly in app.layers))

    # ---- 回归: 铜层最高层号提升为底层; 显式 t/b 后缀不参与推断 ----
    g = "%FSLAX34Y34*%\n%MOMM*%\n%ADD10C,0.2*%\nD10*\nX10000Y10000D03*\nM02*\n"
    lp1 = os.path.join(tmp, "l1p")
    lp4 = os.path.join(tmp, "l4p")
    lpb = os.path.join(tmp, "l1pb")
    lp8t = os.path.join(tmp, "l8pt")
    for _p in (lp1, lp4, lpb, lp8t):
        io.open(_p, "w", encoding="ascii").write(g)
    app._clear()
    app.add_layer(lp1)
    app.add_layer(lp4)
    app._promote_bottom_copper()
    check("promote max copper to bottom",
          (app.layers[1].role or {}).get("side") == "bottom")
    app._clear()
    app.add_layer(lpb)
    app.add_layer(lp8t)
    app._promote_bottom_copper()
    check("explicit t/b suffix kept",
          (app.layers[0].role or {}).get("side") == "bottom"
          and (app.layers[1].role or {}).get("side") == "top")

    # ---- 回归: 宏光圈 ap_cache 不能把不同直径的 flash 串用成一个 ----
    macro_corpus = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools", "_corpus", "06_macro.gbr")
    if os.path.isfile(macro_corpus):
        d = parse_gerber(macro_corpus)
        ly = gv.GerberLayer("macro", d, "#ffffff")
        lb = ly.local_bbox()
        Image2, ImageDraw2, _ = app._PIL()
        minx, miny, maxx, maxy = lb
        sc = min(40.0, gv.MAX_BASE / max(maxx - minx, maxy - miny, 1e-6))
        W = int(round((maxx - minx) * sc)) + 1
        H = int(round((maxy - miny) * sc)) + 1

        def render(cache):
            im = Image2.new("L", (W, H), 0)
            T = gv.GerberViewer._make_xform(lb, sc, H, False, (0.0, 0.0))
            app._draw_prims(ImageDraw2.Draw(im), d, ly.kind, T, sc, 255, 0,
                            ap_cache=cache)
            return im.tobytes()

        check("macro ap_cache diameters", render({}) == render(None))

    test_role_override(app, root, tmp)
    test_compare_workflow(app, root, tmp)
    test_measure_workflow(app, root, tmp)

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
