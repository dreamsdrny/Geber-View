# -*- coding: utf-8 -*-
"""P9: 快速路径按命令形状分级匹配

通用正则 (5 个可选捕获组) 实测 1.43 us/次, 而专用紧正则只要 0.30 us。
按出现频率分级: X..Y..D / 单轴 D / 通用。

另外把每命令 6~7 次 group() 改成 1 次 groups() (1.74 -> 1.50 us)。
"""
import io
import sys

R = "gerber_reader.py"

OLD_HEAD = '''        if kind == "cmd" and sr is None:
            m = _FAST_RE.match(tok[1])
            if m is not None and (m.group(2) is not None or m.group(3) is not None):
                s_g = m.group(1)
                s_x = m.group(2)
                s_y = m.group(3)
                s_i = m.group(4)
                s_j = m.group(5)
                op = int(m.group(6))
'''

NEW_HEAD = '''        if kind == "cmd" and sr is None:
            cs = tok[1]
            s_g = s_x = s_y = s_i = s_j = s_op = None
            m = _RE_XY.match(cs)                 # 最常见: X..Y..D0x
            if m is not None:
                s_x, s_y, s_op = m.groups()
            else:
                m = _RE_ONE.match(cs)            # 次常见: 单轴 X..D0x / Y..D0x
                if m is not None:
                    key, val, s_op = m.groups()
                    if key == "X":
                        s_x = val
                    else:
                        s_y = val
                else:
                    m = _FAST_RE.match(cs)       # 通用: 可带 G/I/J
                    if m is not None:
                        s_g, s_x, s_y, s_i, s_j, s_op = m.groups()
            if s_op is not None:
                op = int(s_op)
'''

PATCHES = [
    ('''_FAST_RE = re.compile(
    r"^(?:G0?([123]))?(?:X(-?\\d+))?(?:Y(-?\\d+))?(?:I(-?\\d+))?"
    r"(?:J(-?\\d+))?D0?([123])$")''',
     '''_FAST_RE = re.compile(
    r"^(?:G0?([123]))?(?:X(-?\\d+))?(?:Y(-?\\d+))?(?:I(-?\\d+))?"
    r"(?:J(-?\\d+))?D0?([123])$")
# 分级匹配: 按出现频率从高到低, 越常见越窄 (实测窄正则快 4 倍)
_RE_XY = re.compile(r"^X(-?\\d+)Y(-?\\d+)D0?([123])$")
_RE_ONE = re.compile(r"^([XY])(-?\\d+)D0?([123])$")'''),
    (OLD_HEAD, NEW_HEAD),
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
    print("[OK] %s: tiered regex" % R)
    return 0


if __name__ == "__main__":
    sys.exit(main())
