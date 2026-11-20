"""Regression tests against known geometry and workflow failure cases."""
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from gerber_diff import _dilate_mm, compare, pair_layers
from gerber_project import load, save
from gerber_reader import PrimStore, _arc_sample, parse_excellon, parse_gerber
from gerber_report import manufacturing, write_diff_csv
from gerber_viewer import GerberLayer, GerberViewer
import gerber_native
import gerber_reader


def viewer():
    obj = GerberViewer.__new__(GerberViewer)
    obj._pil = None
    obj._stop = threading.Event()
    obj._diff_cancel = threading.Event()
    obj._diff_generation = 0
    return obj


def layer(name, x, role):
    store = PrimStore()
    store.push(1, x, 20, x, 20, .15, 10, 0, 0)
    data = {"file": name, "bbox": [x, 20, x, 20], "primitives": store,
            "unit": "mm", "apertures": {10: {"shape": "C", "params": [.15]}},
            "_role": role}
    return GerberLayer(name, data, "#ffffff")


class AccuracyTests(unittest.TestCase):
    def test_full_circle_and_region_arc(self):
        arc = _arc_sample(1, 0, 1, 0, -1, 0, False, 16)
        self.assertAlmostEqual(min(x for x, _ in arc), -1)
        self.assertAlmostEqual(max(y for _, y in arc), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "region.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSLAX34Y34*%%MOMM*%G75*G36*X10000Y00000D02*"
                        "G03X00000Y10000I-10000J00000D01*X00000Y00000D01*G37*M02*")
            data = parse_gerber(path)
            pts = data["primitives"].extras[0][0]
            self.assertGreater(len(pts), 10)
            self.assertTrue(any(x > .6 and y > .6 for x, y in pts))
            self.assertFalse(data["warnings"])
            self.assertTrue(any("圆弧" in n for n in data["notes"]))

    def test_supported_formats_are_information_not_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "standard.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSTAX34Y34*%%MOMM*%%ASAXBY*%%ADD10C,0.2*%"
                        "%SFA2B2*%%MIA1B0*%%IR90*%%SRX2Y1I2.0J0.0*%"
                        "D10*X1Y1D03*%SR*%M02*")
            data = parse_gerber(path)
            self.assertEqual(data["warnings"], [])
            self.assertEqual(data["feature_count"], 2)
            for feature in ("轴选择", "尾随零", "比例", "镜像", "旋转", "拼版"):
                self.assertTrue(any(feature in n for n in data["notes"]), feature)
            ly = GerberLayer("standard", data, "#ffffff")
            self.assertIn("信息:", ly.detail_text())
            self.assertIn("信息:", manufacturing([ly]))

    def test_real_parser_risks_remain_warnings(self):
        cases = {
            "axes": "%FSLAX34Y34*%%MOMM*%%ASAYBX*%",
            "unknown": "%FSLAX34Y34*%%MOMM*%%ZZTEST*%",
            "format": "%MOMM*%",
            "unit": "%FSLAX34Y34*%",
            "macro": "%FSLAX34Y34*%%MOMM*%%ADD10MISSING*%",
            "negative": "%FSLAX34Y34*%%MOMM*%%TF.FilePolarity,Negative*%",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, header in cases.items():
                with self.subTest(name=name):
                    path = os.path.join(tmp, name + ".gbr")
                    with open(path, "w", encoding="ascii") as f:
                        f.write(header + "%ADD11C,0.2*%D10*X10000Y10000D03*M02*")
                    self.assertTrue(parse_gerber(path)["warnings"])

    def test_single_pad_is_not_an_abnormal_board_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pad.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSLAX34Y34*%%MOMM*%%ADD10C,2.0*%D10*X10000Y10000D03*M02*")
            data = parse_gerber(path)
            self.assertFalse(data["warnings"])
            self.assertEqual(GerberLayer("pad", data, "#fff").local_bbox(), [0, 0, 2, 2])

    def test_decimal_drill_and_fixed_width_zero_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "decimal.drl")
            with open(path, "w", encoding="ascii") as f:
                f.write("M48\nMETRIC,TZ\nT1C0.3\n%\nT1\nX10.0Y10.0\nX20.0Y20.0\nM30\n")
            data = parse_excellon(path)
            self.assertEqual(data["warnings"], [])
            self.assertEqual(data["hole_count"], 2)
            self.assertTrue(any("小数点" in n for n in data["notes"]))
            path = os.path.join(tmp, "fixed.drl")
            with open(path, "w", encoding="ascii") as f:
                f.write("M48\n;FILE_FORMAT=3:3\nMETRIC,TZ\nT1C0.3\n%\nT1\n"
                        "X010000Y010000\nX020000Y020000\nM30\n")
            data = parse_excellon(path)
            self.assertFalse(any("坐标特征" in w for w in data["warnings"]))
            self.assertEqual([h["x"] for h in data["holes"]], [10, 20])

    def test_native_and_python_diagnostics_agree(self):
        if not gerber_native.available():
            self.skipTest("optional native backend unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "transforms.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSTAX34Y34*%%MOMM*%%ADD10C,0.2*%%SFA2B2*%%MIA1B0*%%IR90*%"
                        "D10*X1Y1D03*X2Y2D03*M02*")
            native = gerber_native.parse(path)
            self.assertIsNotNone(native)
            with patch.object(gerber_reader, "_native_backend", return_value=None):
                python = parse_gerber(path)
            for key in ("warnings", "notes", "bbox", "feature_count"):
                self.assertEqual(native[key], python[key], key)

    def test_scale_notes_do_not_mutate_cached_diagnostics(self):
        source = {"warnings": ["单位需核对"], "notes": ["已处理镜像"], "unit": "mm"}
        scaled = gerber_reader.scale_fab_data(source, 2)
        scaled["notes"].append("比例校正")
        scaled["warnings"].append("新警告")
        self.assertEqual(source["notes"], ["已处理镜像"])
        self.assertEqual(source["warnings"], ["单位需核对"])

    def test_slot_render_and_aperture_hole(self):
        v = viewer()
        image = Image.new("L", (120, 120), 0)
        draw = ImageDraw.Draw(image)
        v._draw_prims(draw, {"holes": [{"x": 10, "y": 20, "x2": 100, "y2": 20,
                                        "diameter_mm": 10, "is_slot": True}]},
                      "drill", lambda x, y: (x, y), 1, 255, 0)
        self.assertEqual(image.getpixel((50, 20)), 255)
        store = PrimStore()
        store.push(1, 60, 60, 60, 60, 40, 10, 0, 0)
        data = {"primitives": store, "unit": "mm",
                "apertures": {10: {"shape": "C", "params": [40, 10]}}}
        image.putpixel((60, 60), 180)
        v._draw_prims(draw, data, "gerber", lambda x, y: (x, y), 1, 255, 0)
        self.assertEqual(image.getpixel((60, 60)), 180)
        self.assertEqual(image.getpixel((70, 60)), 255)

    def test_region_hole_and_panel_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "region.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSLAX34Y34*%%MOMM*%%SRX2Y1I20.0J0.0*%G36*"
                        "X000000Y000000D02*X100000Y000000D01*X100000Y100000D01*"
                        "X000000Y100000D01*X000000Y000000D01*"
                        "X030000Y030000D02*X070000Y030000D01*X070000Y070000D01*"
                        "X030000Y070000D01*X030000Y030000D01*G37*%SR*%M02*")
            data = parse_gerber(path)
            image = Image.new("L", (320, 120), 0)
            viewer()._draw_prims(ImageDraw.Draw(image), data, "gerber",
                                  lambda x, y: (x * 10, y * 10), 10, 255, 0)
            self.assertEqual(image.getpixel((10, 10)), 255)
            self.assertEqual(image.getpixel((50, 50)), 0)
            self.assertEqual(image.getpixel((210, 10)), 255)
            self.assertEqual(image.getpixel((250, 50)), 0)

    def test_render_result_target_is_layer(self):
        v = viewer()
        a, b = layer("a", 1, {}), layer("b", 2, {})
        a.pending = b.pending = True
        a.token = b.token = 1
        v.layers = [a, b]
        v._lru_clock = 0
        v._schedule_render = lambda: None
        v._handle_ui(("base", b, 1, "b bitmap", 1, (0, 0, 1, 1)))
        self.assertIsNone(a.base_img)
        self.assertEqual(b.base_img, "b bitmap")

    def test_layer_pairing_and_small_differences(self):
        v = viewer()
        top = {"kind": "copper", "side": "top"}
        bottom = {"kind": "copper", "side": "bottom"}
        a = [layer("top.gtl", 20.1, top), layer("bottom.gbl", 30, bottom)]
        b = [layer("top.gtl", 30, top), layer("bottom.gbl", 20.3, bottom)]
        pairs, notes = pair_layers(a, b)
        result = v._compute_diff({"pairs": pairs, "bbox": (0, 0, 100, 100),
                                  "resolution": .05, "tolerance": 0, "notes": notes,
                                  "namesA": [x.name for x in a], "namesB": [x.name for x in b]})
        self.assertGreater(result["total"], 0)
        self.assertEqual(len({p["layer"] for p in result["points"]}), 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "diff.csv")
            write_diff_csv(path, result)
            with open(path, encoding="utf-8-sig") as f:
                self.assertIn("top.gtl", f.read())

    def test_project_and_manufacturing_report(self):
        ly = layer("top.gtl", 1, {"kind": "copper", "side": "top"})
        ly.group = "A"; ly.offset = [1.5, -2]; ly.scale_factor = 2
        with tempfile.TemporaryDirectory() as tmp:
            ly.raw_data["file"] = os.path.join(tmp, "top.gtl")
            path = os.path.join(tmp, "board.egv.json")
            save(path, [ly])
            record = load(path)[0]
            self.assertEqual(record["path"], ly.raw_data["file"])
            self.assertEqual(record["offset"], [1.5, -2])
            self.assertEqual(record["scale"], 2)
            self.assertIn("比例: ×2", manufacturing([ly]))

    def test_limits_and_cancel(self):
        with self.assertRaises(InterruptedError):
            compare([("x", None, None)], (0, 0, 1, 1), .1, 0,
                    lambda *_: None, lambda: True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "panel.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*"
                        "%SRX2000001Y1I1.0J0.0*%X10000Y10000D03*%SR*%M02*")
            with self.assertRaisesRegex(ValueError, "拼版"):
                parse_gerber(path)

    def test_tolerance_does_not_round_up(self):
        img = Image.new("L", (7, 7), 0)
        img.putpixel((3, 3), 255)
        self.assertEqual(_dilate_mm(img, .05, .01).getpixel((4, 3)), 0)
        expanded = _dilate_mm(img, .05, .05)
        self.assertEqual(expanded.getpixel((4, 3)), 255)
        self.assertEqual(expanded.getpixel((4, 4)), 0)


if __name__ == "__main__":
    unittest.main()
