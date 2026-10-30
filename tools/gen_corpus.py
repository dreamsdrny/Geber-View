# -*- coding: utf-8 -*-
"""生成覆盖解析器全部分支的 Gerber 语料库 (等价性对拍用)"""
import io
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_corpus")


def w(name, lines):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    io.open(p, "w", encoding="ascii", newline="\n").write("\n".join(lines) + "\n")
    return p


def basic():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.150*%", "%ADD11R,1.2X0.8*%",
         "%ADD12O,2.0X1.0*%", "%ADD13P,1.5X6X15*%", "%ADD14C,0.300*%", "D10*"]
    for i in range(40):
        y = 1 + i * 0.5
        L += ["X%dY%dD02*" % (1000, int(y * 1000)),
              "X%dY%dD01*" % (58000, int(y * 1000))]
    L += ["D11*"]
    for i in range(10):
        L += ["X%dY%dD03*" % (int((2 + i * 5) * 1000), 20000)]
    L += ["D12*", "X5000Y25000D03*", "D13*", "X15000Y25000D03*", "D14*",
          "X25000Y30000D03*",
          "X1000Y1000D02*", "X2000Y1000D01*", "X3000Y1000D01*",
          "M02*"]
    return w("01_basic.gbr", L)


def inch_trailing():
    L = ["%FSTAX24Y24*%", "%MOIN*%", "%ADD10C,0.010*%", "D10*"]
    for i in range(20):
        y = i * 0.05
        L += ["X%dY%dD02*" % (100, int(y * 10000)),
              "X%dY%dD01*" % (3000, int(y * 10000))]
    L += ["D10*", "X500Y500D03*", "M02*"]
    return w("02_inch_fsta.gbr", L)


def no_unit_mm():
    L = ["%FSLAX34Y34*%", "%ADD10C,0.2*%", "D10*"]
    for i in range(15):
        L += ["X%dY%dD02*" % (1000, int((2 + i) * 1000)),
              "X%dY%dD01*" % (40000, int((2 + i) * 1000))]
    L += ["M02*"]
    return w("03_nounit_mm.gbr", L)


def no_unit_inch():
    L = ["%FSLAX24Y24*%", "%ADD10C,0.010*%", "D10*"]
    for i in range(15):
        L += ["X%dY%dD02*" % (100, int((10 + i) * 100)),
              "X%dY%dD01*" % (3000, int((10 + i) * 100))]
    L += ["M02*"]
    return w("04_nounit_inch.gbr", L)


def merged_ext():
    L = ["%FSLAX55Y55*MOIN*%", "%ADD10C,0.01*%", "D10*",
         "%IR0*IPPOS*OFA0B0*MIA0B0*SFA1B1*%"]
    for i in range(12):
        L += ["X%dY%dD02*" % (1000, int(i * 5000)),
              "X%dY%dD01*" % (200000, int(i * 5000))]
    L += ["M02*"]
    return w("05_merged_ext.gbr", L)


def macro_ap():
    L = ["%FSLAX34Y34*%", "%MOMM*%",
         "%AMCIRC*1,1,$1,0,0*%",
         "%AMRNDRECT*21,1,$1,$2,0,0,0*%",
         "%AMLINE*20,1,$1,0,0,$2,$3,0*%",
         "%AMOUT*4,1,4,0,0,$1,0,0,$2,$1,$2,0,$1*%",
         "%AMPOLY*5,1,6,0,0,$1,0*%",
         "%AMTHRM*7,0,0,$1,$2,$3,45*%",
         "%ADD10CIRC,2.0*%", "%ADD11RNDRECT,1.6X0.9*%",
         "%ADD12LINE,0.3X3.0X0.3*%", "%ADD13OUT,2.0X1.0*%",
         "%ADD14POLY,1.5*%", "%ADD15THRM,1.2X0.6X0.3*%", "%ADD16C,0.25*%",
         "D10*", "X10000Y10000D03*", "D11*", "X20000Y10000D03*",
         "D12*", "X30000Y10000D03*", "D13*", "X40000Y10000D03*",
         "D14*", "X10000Y20000D03*", "D15*", "X20000Y20000D03*",
         "D16*", "X10000Y30000D02*", "X30000Y30000D01*",
         "M02*"]
    return w("06_macro.gbr", L)


def step_repeat():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "D10*",
         "%SRX3Y2I10.0J8.0*%"]
    for i in range(5):
        L += ["X%dY%dD02*" % (1000, int((1 + i) * 1000)),
              "X%dY%dD01*" % (8000, int((1 + i) * 1000))]
    L += ["X1000Y1000D03*", "%SR*%", "M02*"]
    return w("07_sr.gbr", L)


def arcs():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "D10*",
         "G75*",
         "X10000Y10000D02*", "G03X20000Y20000I0J10000D01*",
         "G02X30000Y10000I5000J0D01*",
         "X10000Y30000D02*", "G03X10000Y30000I5000J0D01*",
         "G74*", "X40000Y10000D02*", "G03X50000Y10000I5000J0D01*",
         "G75*", "X10000Y40000D02*", "G01X20000Y40000D01*", "M02*"]
    return w("08_arcs.gbr", L)


def regions():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "D10*",
         "G36*", "X1000Y1000D02*", "X20000Y1000D01*", "X20000Y8000D01*",
         "X1000Y8000D01*", "X1000Y1000D01*", "G37*",
         "G36*", "X25000Y1000D02*", "X35000Y1000D01*", "X30000Y7000D01*",
         "X25000Y1000D01*", "G37*",
         "G36*", "X1000Y15000D02*", "X5000Y15000D01*", "G37*",
         "X40000Y20000D02*", "X45000Y20000D01*", "M02*"]
    return w("09_region.gbr", L)


def polarity():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,5.0*%", "%ADD11C,1.0*%",
         "%LPD*%", "D10*", "X20000Y20000D03*",
         "%LPC*%", "D11*", "X20000Y20000D03*",
         "%LPD*%", "D11*", "X30000Y20000D03*", "M02*"]
    return w("10_polarity.gbr", L)


def x2_attrs():
    L = ["%FSLAX34Y34*%", "%MOMM*%",
         "%TF.FileFunction,Copper,L1,Top*%",
         "%TF.FilePolarity,Positive*%",
         "%TF.Part,Single*%",
         "%TF.GenerationSoftware,TestCAM,1.0*%",
         "%TF.CreationDate,2026-01-01T00:00:00*%",
         "%TA.AperFunction,SMDPad,CuDef*%",
         "%ADD10C,0.9*%", "%ADD11C,0.25*%", "D10*"]
    for net, ref, pin, x in (("GND", "U1", "1", 5000), ("VCC", "U1", "2", 12000),
                             ("GND", "U2", "3", 19000)):
        L += ["%%TO.N,%s*%%" % net, "%%TO.P,%s,%s*%%" % (ref, pin),
              "X%dY%dD03*" % (x, 8000), "%%TD*%%"]
    L += ["D11*"]
    for x0, x1 in ((5000, 12000), (12000, 19000)):
        L += ["%TO.N,GND*%", "X%dY%dD02*" % (x0, 8000), "X%dY%dD01*" % (x1, 8000),
              "%TD*%"]
    L += ["%TO.C,R5*%", "X25000Y12000D03*", "%TD*%", "M02*"]
    return w("11_x2.gbr", L)


def transforms():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.3*%",
         "%OFA2.0B1.0*%", "%SFA2.0B2.0*%", "%MIA1B0*%", "%IR90*%", "D10*"]
    for i in range(8):
        L += ["X%dY%dD02*" % (1000 + i * 1000, 1000),
              "X%dY%dD01*" % (1000 + i * 1000, 6000)]
    L += ["X5000Y5000D03*", "M02*"]
    return w("12_transform.gbr", L)


def incremental():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "G91*", "D10*",
         "X1000Y1000D02*"]
    for i in range(10):
        L += ["X2000Y1000D01*"]
    L += ["G90*", "X50000Y50000D02*", "X55000Y50000D01*",
          "X3000Y3000D02*", "D03*", "M02*"]
    return w("13_incremental.gbr", L)


def edge_cases():
    L = ["%FSLAX34Y34*%", "%MOMM*%",
         "G04 this is a comment with * inside*%*%",
         "%ADD10C,0.2*%", "D10*",
         "X1000Y1000D02*", "X1000Y1000D01*",          # 零长线
         "X2000D02*", "X3000D01*",                    # 只给 X
         "Y5000D01*",                                 # 只给 Y
         "X4000Y4000D03*",                            # 有 D03 但光圈存在
         "D11*", "X5000Y5000D03*",                    # 未定义光圈 -> error
         "D10*", "X6000Y6000D02*", "M02*"]
    return w("14_edge.gbr", L)


def big_plane():
    L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.100*%", "%ADD11C,0.6*%", "D10*"]
    for i in range(15000):
        x = (i % 300) * 0.2
        y = (i // 300) * 1.0
        L += ["X%dY%dD02*" % (int(x * 10000), int(y * 10000)),
              "X%dY%dD01*" % (int((x + 0.15) * 10000), int(y * 10000))]
    L += ["D11*"]
    for i in range(500):
        L.append("X%dY%dD03*" % (int((i % 50) * 1.2 * 10000),
                                 int((i // 50) * 1.2 * 10000)))
    L.append("M02*")
    return w("15_big.gbr", L)


def main():
    files = [basic(), inch_trailing(), no_unit_mm(), no_unit_inch(), merged_ext(),
             macro_ap(), step_repeat(), arcs(), regions(), polarity(), x2_attrs(),
             transforms(), incremental(), edge_cases(), big_plane()]
    for p in files:
        print("%8d  %s" % (os.path.getsize(p), os.path.basename(p)))
    print("corpus ->", OUT)


if __name__ == "__main__":
    sys.exit(main())
