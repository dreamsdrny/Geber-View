# -*- coding: utf-8 -*-
"""端到端回归: 真实 Tk 窗口 + 渲染 + 高亮 + Solo + 测距 + 性能数字"""
import io
import os
import shutil
import sys
import tempfile
import time

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

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
