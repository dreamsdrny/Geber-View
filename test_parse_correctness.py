# -*- coding: utf-8 -*-
"""P1 解析正确性回归: 单位劫持 / 缓存键污染 / 小写命令 / Excellon 零省略告警 /
宏比较运算符与 thermal 旋转 / G00 快移假孔。每条对应一个已修复的静默错误。"""
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerber_reader as gr
from gerber_reader import parse_gerber, parse_excellon, _macro_eval


@contextmanager
def force_python():
    """临时禁用 Rust 原生后端, 确定性走纯 Python 路径 (这些 bug 都在 Python 解析器)。"""
    old_native, old_tried = gr._NATIVE, gr._NATIVE_TRIED
    gr._NATIVE, gr._NATIVE_TRIED = None, True
    gr.clear_parse_cache()
    try:
        yield
    finally:
        gr._NATIVE, gr._NATIVE_TRIED = old_native, old_tried
        gr.clear_parse_cache()


def _write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="ascii") as f:
        f.write(text)
    return p


class UnitHijack(unittest.TestCase):
    def test_net_name_containing_moin_does_not_switch_unit(self):
        # %TO.N,MOIN_5V*% 是网络名, 不是单位声明; 旧版 .search 会把单位中途切成英寸 (×25.4)
        with tempfile.TemporaryDirectory() as tmp, force_python():
            p = _write(tmp, "net.gbr",
                       "%FSLAX34Y34*%\n%MOMM*%\n%ADD10C,0.2*%\n"
                       "%TO.N,MOIN_5V*%\nD10*\nX10000Y10000D03*\nM02*\n")
            d = parse_gerber(p)
            self.assertEqual(d["unit"], "mm")
            b = d["bbox"]
            self.assertIsNotNone(b)
            self.assertLess(b[2], 2.0)          # 闪装在 1.0mm; 若被劫持成英寸会到 ~25.5
            self.assertAlmostEqual((b[0] + b[2]) / 2, 1.0, delta=0.15)

    def test_layer_name_containing_momm_does_not_switch_unit(self):
        with tempfile.TemporaryDirectory() as tmp, force_python():
            p = _write(tmp, "ly.gbr",
                       "%FSLAX34Y34*%\n%MOIN*%\n%ADD10C,0.01*%\n"
                       "%TO.C,MOMM_BRACKET*%\nD10*\nX10000Y10000D03*\nM02*\n")
            d = parse_gerber(p)
            self.assertEqual(d["unit"], "inch")  # 保持英寸, 不被 MOMM 子串切成 mm


class CacheKey(unittest.TestCase):
    def test_cache_key_not_clobbered_by_tf_or_single_axis(self):
        # %TF 属性与单轴移动都会复用局部变量; 旧版把文件缓存键覆盖成字符串,
        # 导致 X2/单轴文件永不命中缓存并污染 LRU。
        with tempfile.TemporaryDirectory() as tmp, force_python():
            p = _write(tmp, "x2.gbr",
                       "%FSLAX34Y34*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
                       "%ADD10C,0.2*%\nG36*\nX0Y0D02*\nX10000Y0D01*\n"
                       "X10000Y10000D01*\nX0Y10000D01*\nX0Y0D01*\nG37*\n"
                       "D10*\nX50000D01*\nY50000D01*\nM02*\n")
            parse_gerber(p)
            keys = list(gr._PARSE_CACHE.keys())
            self.assertEqual(len(keys), 1)
            self.assertIsInstance(keys[0], tuple)
            self.assertEqual(keys[0][0], os.path.abspath(p))
            parse_gerber(p)                       # 第二次应命中同一缓存键
            self.assertEqual(len(gr._PARSE_CACHE), 1)


class LowercaseCommands(unittest.TestCase):
    def test_lowercase_commands_parse_same_as_uppercase(self):
        with tempfile.TemporaryDirectory() as tmp, force_python():
            up = _write(tmp, "up.gbr",
                        "%FSLAX34Y34*%\n%MOMM*%\n%ADD10C,0.2*%\n"
                        "D10*\nX10000Y10000D03*\nM02*\n")
            lo = _write(tmp, "lo.gbr",
                        "%FSLAX34Y34*%\n%MOMM*%\n%ADD10C,0.2*%\n"
                        "d10*\nx10000y10000d03*\nm02*\n")
            du, dl = parse_gerber(up), parse_gerber(lo)
            self.assertIsNotNone(dl["bbox"])
            self.assertEqual(du["bbox"], dl["bbox"])
            self.assertAlmostEqual((dl["bbox"][0] + dl["bbox"][2]) / 2, 1.0, delta=0.15)


class ExcellonZeroWarning(unittest.TestCase):
    def test_short_fixed_width_tz_warns(self):
        # 定宽 5 位 < 格式 6 位, 且声明 TZ: L/T 两种解读差 10 倍, 必须告警 (不再静默)
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, "short.drl",
                       "M48\n;FILE_FORMAT=2:4\nMETRIC,TZ\nT1C0.3\n%\nT1\n"
                       "X10000Y10000\nX20000Y20000\nM30\n")
            d = parse_excellon(p)
            self.assertTrue(any("10 倍" in w for w in d["warnings"]),
                            d["warnings"])

    def test_full_width_fixed_tz_stays_silent(self):
        # 定宽 == 全宽时 L/T 解读一致, 不应打扰 (与 test_accuracy 既有契约一致)
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, "full.drl",
                       "M48\n;FILE_FORMAT=3:3\nMETRIC,TZ\nT1C0.3\n%\nT1\n"
                       "X010000Y010000\nX020000Y020000\nM30\n")
            d = parse_excellon(p)
            self.assertFalse(any("10 倍" in w for w in d["warnings"]))
            self.assertEqual([h["x"] for h in d["holes"]], [10, 20])


class MacroEval(unittest.TestCase):
    def test_compare_operators(self):
        self.assertEqual(_macro_eval("1<2", {}), 1.0)
        self.assertEqual(_macro_eval("2<1", {}), 0.0)
        self.assertEqual(_macro_eval("3>2", {}), 1.0)
        self.assertEqual(_macro_eval("2>=2", {}), 1.0)
        self.assertEqual(_macro_eval("2<=1", {}), 0.0)
        self.assertEqual(_macro_eval("1!=2", {}), 1.0)
        self.assertEqual(_macro_eval("1=1", {}), 1.0)   # Gerber '=' 归一化为 '=='
        self.assertEqual(_macro_eval("1=2", {}), 0.0)

    def test_ast_cache_reuses_with_different_variables(self):
        # 缓存的是 AST (只读), 变量每次传入; 同表达式不同变量须各自正确。
        self.assertEqual(_macro_eval("$1+1", {"_v1": 2.0}), 3.0)
        self.assertEqual(_macro_eval("$1+1", {"_v1": 5.0}), 6.0)
        self.assertEqual(_macro_eval("$1x$2", {"_v1": 3.0, "_v2": 4.0}), 12.0)
        self.assertEqual(_macro_eval("$1x$2", {"_v1": 2.0, "_v2": 5.0}), 10.0)

    def test_thermal_offset_rotation_stays_compact(self):
        # 偏心 thermal (cx=2,cy=1) 旋转 45°: 缺口须绕自身中心旋转, 整体仍紧凑于外圆;
        # 旧版绕宏原点旋转会把缺口甩飞, 包围盒远大于外圆直径。
        # code 7 参数序: cx, cy, 外径, 内径, 缺口宽, 旋转角。
        with tempfile.TemporaryDirectory() as tmp, force_python():
            p = _write(tmp, "th.gbr",
                       "%FSLAX34Y34*%\n%MOMM*%\n"
                       "%AMTH*7,2.0,1.0,1.0,0.6,0.2,45*%\n%ADD10TH*%\n"
                       "D10*\nX0Y0D03*\nM02*\n")
            d = parse_gerber(p)
            b = d["bbox"]
            self.assertIsNotNone(b)
            w, h = b[2] - b[0], b[3] - b[1]
            self.assertLess(w, 1.4, f"thermal 包围盒宽 {w} 应≈外圆直径 1.0")
            self.assertLess(h, 1.4, f"thermal 包围盒高 {h} 应≈外圆直径 1.0")
            self.assertAlmostEqual((b[0] + b[2]) / 2, 2.0, delta=0.2)
            self.assertAlmostEqual((b[1] + b[3]) / 2, 1.0, delta=0.2)


class DrillRapid(unittest.TestCase):
    def test_g00_rapid_does_not_create_hole(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, "rapid.drl",
                       "M48\nMETRIC,TZ\nT1C0.3\n%\nG90\nG05\nT1\n"
                       "G00X0Y0\nX10.0Y10.0\nX20.0Y20.0\nM30\n")
            d = parse_excellon(p)
            self.assertEqual(d["hole_count"], 2)        # G00 定位不产生假孔
            self.assertEqual([h["x"] for h in d["holes"]], [10.0, 20.0])


class GridIndexConcurrency(unittest.TestCase):
    def test_parallel_query_no_duplicates(self):
        # query 的 seen/gen 是共享态; UI 线程与渲染 worker 并发调用时,
        # 无锁会相互覆盖代际标记 -> 同一查询返回重复图元。加锁后必须无重复且升序。
        import threading
        from gerber_viewer import _GridIndex
        with tempfile.TemporaryDirectory() as tmp, force_python():
            L = ["%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "D10*"]
            for i in range(25000):
                x = (i % 500) * 0.2
                y = (i // 500) * 0.2
                L.append("X%dY%dD03*" % (int(x * 10000), int(y * 10000)))
            L.append("M02*")
            p = _write(tmp, "big.gbr", "\n".join(L))
            d = parse_gerber(p)
            idx = _GridIndex(d["primitives"], d, d["bbox"])
            errors = []

            def worker():
                for _ in range(120):
                    r = idx.query(0.0, 0.0, 49.0, 4.9)
                    if r is None:
                        continue
                    if len(r) != len(set(r)):
                        errors.append("dup")
                    if r != sorted(r):
                        errors.append("unsorted")

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()