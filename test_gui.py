# -*- coding: utf-8 -*-
"""端到端回归: 真实 Tk 窗口 + 渲染 + 高亮 + Solo + 测距 + 性能数字"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk

from gerber_reader import parse_gerber
import gerber_viewer as gv


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
    tmp = tempfile.gettempdir()
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
        h = im.convert("RGBA").split()
        px = list(im.convert("RGBA").getdata())
        bg = gv.BG_RGB
        n = sum(1 for p in px if (p[0], p[1], p[2]) != bg)
        return n / float(len(px))

    print("ink ratio %.3f" % ink(img))

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
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
