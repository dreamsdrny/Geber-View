"""
Evan_Gerber_View 图标生成器 (Qiao · 紫色)
==========================================
用 PIL 生成清晰的多尺寸 Windows 图标:
  Evan_Gerber_View.ico (16/24/32/48/64/128/256)
  Evan_Gerber_View.png (256x256, 任务栏/README 用)

设计: 紫色圆角方形渐变底 + 白色粗体 "Qiao" + PCB 走线点缀 (Gerber 主题)。

运行:
    python make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = [16, 24, 32, 48, 64, 128, 256]
TEXT = "Qiao"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    r"C:\Windows\Fonts\verdanab.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]

# 紫色主题 (深紫 -> 亮紫/品红 对角渐变)
TOP = (109, 40, 217)      # #6D28D9
BOT = (192, 38, 211)      # #C026D3
NODE = (196, 181, 253)    # #C4B5FD 淡紫 (走线节点)


def _load_font(size):
    for fp in FONT_CANDIDATES:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _fit_font(draw, text, px, max_w, max_h):
    """选取能放入 (max_w, max_h) 的最大字号"""
    lo, hi, best = 8, int(px * 0.9), 8
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(mid)
        tb = draw.textbbox((0, 0), text, font=font)
        if (tb[2] - tb[0]) <= max_w and (tb[3] - tb[1]) <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _load_font(best)


def make_master(px=512):
    """渲染高分辨率母版 (再降采样到各尺寸, 保证清晰)"""
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    radius = int(px * 0.22)

    # 紫色对角渐变 + 圆角遮罩
    grad = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(px):
        gd.line([(0, y), (px, y)], fill=_lerp(TOP, BOT, y / max(1, px - 1)) + (255,))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # PCB 走线点缀 (白色半透明 + 淡紫节点)
    acc = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ad = ImageDraw.Draw(acc)
    w = max(2, int(px * 0.02))
    line_col = (255, 255, 255, 60)
    ad.line([(int(px * 0.10), int(px * 0.80)), (int(px * 0.24), int(px * 0.80)),
             (int(px * 0.32), int(px * 0.88)), (int(px * 0.32), int(px * 0.94))],
            fill=line_col, width=w, joint="curve")
    ad.ellipse([int(px * 0.32) - w, int(px * 0.94) - w,
                int(px * 0.32) + w, int(px * 0.94) + w], fill=NODE + (210,))
    ad.line([(int(px * 0.90), int(px * 0.20)), (int(px * 0.76), int(px * 0.20)),
             (int(px * 0.68), int(px * 0.12)), (int(px * 0.68), int(px * 0.06))],
            fill=line_col, width=w, joint="curve")
    ad.ellipse([int(px * 0.68) - w, int(px * 0.06) - w,
                int(px * 0.68) + w, int(px * 0.06) + w], fill=NODE + (210,))
    img = Image.alpha_composite(img, acc)

    # 白色粗体 "Qiao" (居中 + 阴影)
    d = ImageDraw.Draw(img)
    font = _fit_font(d, TEXT, px, int(px * 0.80), int(px * 0.46))
    tb = d.textbbox((0, 0), TEXT, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = (px - tw) / 2 - tb[0]
    ty = (px - th) / 2 - tb[1] - int(px * 0.015)

    sh = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((tx + px * 0.012, ty + px * 0.02), TEXT,
                            font=font, fill=(30, 10, 60, 130))
    sh = sh.filter(ImageFilter.GaussianBlur(px * 0.012))
    img = Image.alpha_composite(img, sh)
    ImageDraw.Draw(img).text((tx, ty), TEXT, font=font, fill=(255, 255, 255, 255))

    # 顶部高光 (玻璃质感)
    gloss = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        [int(px * 0.06), int(px * 0.06), int(px * 0.94), int(px * 0.52)],
        radius=int(radius * 0.9), fill=(255, 255, 255, 30))
    img = Image.alpha_composite(img, gloss)

    # 外描边 (浅色任务栏上更清晰)
    ImageDraw.Draw(img).rounded_rectangle(
        [1, 1, px - 2, px - 2], radius=radius,
        outline=(255, 255, 255, 70), width=max(2, int(px * 0.008)))
    return img


def main():
    master = make_master(512)
    png_path = os.path.join(HERE, "Evan_Gerber_View.png")
    master.resize((256, 256), Image.LANCZOS).save(png_path, "PNG")

    ico_path = os.path.join(HERE, "Evan_Gerber_View.ico")
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print("WROTE", ico_path, os.path.getsize(ico_path), "bytes")
    print("WROTE", png_path, os.path.getsize(png_path), "bytes")


if __name__ == "__main__":
    main()