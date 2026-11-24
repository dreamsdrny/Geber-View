"""Cross-format comparison and geometric picking regressions."""
import math
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import gerber_diff
from gerber_diff import pair_layers, _dilate_mm
from gerber_layer import identify_layer
from gerber_measure import Feature, distance, nearest_features, primitive_feature, hole_feature
from gerber_project import save, load
from gerber_reader import PrimStore, parse_gerber, parse_aperture_table
from gerber_viewer import GerberViewer, GerberLayer


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def viewer(mode="中心到中心"):
    v = GerberViewer.__new__(GerberViewer)
    v._pil = None
    v._stop = threading.Event()
    v._diff_cancel = threading.Event()
    v._diff_generation = 0
    v._solo = None
    v.layers = []
    v._measure_mode = Var(mode)
    v._measure_on = True
    v._measure_pts = []
    v._measure_features = []
    v._measure_hover = None
    v._measure_cur = None
    v._snap_var = Var(True)
    v.status = Var("")
    v._sc = lambda: (100.,)
    v._ui_scale = lambda: 1.
    v._canvas_to_mm = lambda x, y: (x, y)
    return v


def pad(x, y=0, shape="C", dims=(2.,), code=1):
    store = PrimStore()
    store.push(code, x, y, x, y, dims[0], 10, 0, 0)
    return GerberLayer("pad.gbr", {"file": "pad.gbr", "kind": "gerber", "unit": "mm", "bbox": [x, y, x, y],
                       "primitives": store, "apertures": {10: {"shape": shape, "params": list(dims)}}}, "#fff")


class MeasureTests(unittest.TestCase):
    def test_circle_centres_edges_and_center_edge(self):
        v = viewer()
        a, b = primitive_feature(v, pad(0), 0), primitive_feature(v, pad(5), 0)
        self.assertEqual(distance(*v._feature_measure_pair(a, b)), 5.)
        v._measure_mode.set("边到边")
        self.assertEqual(v._feature_measure_pair(a, b), ((1., 0.), (4., 0.)))
        v._measure_mode.set("中心到边")
        self.assertEqual(distance(*v._feature_measure_pair(a, b)), 4.)

    def test_rectangles_obrounds_slots_and_round_tracks(self):
        v = viewer()
        rect = primitive_feature(v, pad(0, shape="R", dims=(2., 4.)), 0)
        obround = primitive_feature(v, pad(5, shape="O", dims=(4., 2.)), 0)
        self.assertAlmostEqual(distance(*nearest_features(rect, obround)), 2.)
        track = Feature((1,), (0, 0), [[(-3., 0.), (3., 0.)]], .25)
        other = Feature((2,), (0, 2), [[(-3., 2.), (3., 2.)]], .25)
        self.assertAlmostEqual(distance(*nearest_features(track, other)), 1.5)
        self.assertEqual(track.boundary((0, 0)), (0., .25))
        layer = GerberLayer("slots", {"kind": "drill", "holes": [
            {"x": 0., "y": 0., "x2": 4., "y2": 0., "diameter_mm": 1., "is_slot": True}]}, "#fff")
        slot = hole_feature(layer, 0)
        self.assertEqual(slot.center, (2., 0.))
        self.assertAlmostEqual(distance(*nearest_features(slot, primitive_feature(v, pad(7), 0))), 1.5)

    def test_overlap_touch_and_containment_zero(self):
        v = viewer()
        a = primitive_feature(v, pad(0), 0)
        for x in (0., 1., 2.):
            self.assertEqual(distance(*nearest_features(a, primitive_feature(v, pad(x), 0))), 0.)
        big = primitive_feature(v, pad(0, shape="R", dims=(10., 10.)), 0)
        self.assertEqual(distance(*nearest_features(a, big)), 0.)
        crossing = Feature((3,), (0, 0), [[(0., -3.), (0., 3.)]], .1)
        horizontal = Feature((4,), (0, 0), [[(-3., 0.), (3., 0.)]], .1)
        self.assertEqual(distance(*nearest_features(crossing, horizontal)), 0.)

    def test_hole_contours_are_not_filled(self):
        v = viewer()
        ring = primitive_feature(v, pad(0, dims=(4., 2.)), 0)
        self.assertFalse(ring.contains((0., 0.)))
        self.assertTrue(ring.contains((1.5, 0.)))
        small = primitive_feature(v, pad(0, dims=(.2,)), 0)
        self.assertAlmostEqual(distance(*nearest_features(ring, small)), .9, delta=.0011)

    def test_pick_large_body_mirror_offset_and_clear(self):
        v = viewer()
        ly = pad(10, 20, shape="R", dims=(4., 2.))
        ly.mirror_x = True
        ly.offset = [3., 4.]
        v.layers = [ly]
        self.assertEqual(v._snap_point(-8.5, 24.), (-7., 24.))
        ly.visible = False
        self.assertEqual(v._snap_point(-8.5, 24.), (-8.5, 24.))
        ly.visible = True
        ly.data["primitives"].rec[0] = 9
        self.assertIsNone(v._pick_measure_feature(-7., 24.))
        self.assertIsNotNone(v._pick_measure_feature(-8.95, 24., edge=True))

    def test_preview_matches_click_and_no_unsnapped_fallback(self):
        v = viewer("边到边")
        a, b = pad(0), pad(5)
        v.layers = [a, b]
        v._measure_click(SimpleNamespace(x=0, y=0))
        v._on_motion_measure(SimpleNamespace(x=5.9, y=0))
        preview = v._measure_display_points()
        self.assertEqual(preview, [(1., 0.), (4., 0.)])
        self.assertIn("3.0000 mm", v._measure_text())
        v._measure_click(SimpleNamespace(x=5.9, y=0))
        self.assertEqual(preview, v._measure_pts)
        v._measure_click(SimpleNamespace(x=100, y=100))
        self.assertEqual(v._measure_pts, [])
        self.assertIn("未抓取", v.status.get())

    def test_free_point_preview_and_angle(self):
        v = viewer("自由点/夹角")
        v._measure_pts = [(0, 0)]
        v._measure_cur = (3, 4)
        self.assertIn("5.0000 mm", v._measure_text())
        v._measure_cur = None
        v._measure_pts = [(0, 0), (3, 4), (3, 0)]
        self.assertIn("夹角=36.87", v._measure_text())

    def test_rotated_polygon_and_region(self):
        v = viewer()
        polygon = primitive_feature(v, pad(0, shape="P", dims=(2., 4., 45.)), 0)
        self.assertAlmostEqual(polygon.boundary((3., 0))[0], math.sqrt(.5))
        ly = pad(0)
        ly.data["primitives"].rec[0] = 3
        ly.data["primitives"].extras[0] = ([[(0, 0), (10, 0), (10, 10), (0, 10)],
                                                       [(3, 3), (7, 3), (7, 7), (3, 7)]], "", "", "")
        region = primitive_feature(v, ly, 0)
        self.assertTrue(region.contains((1, 1)))
        self.assertFalse(region.contains((5, 5)))
        self.assertEqual(region.center, (5, 5))

    def test_arc_outline_and_track_midpoint_grab(self):
        v = viewer()
        ly = pad(0)
        store = PrimStore()
        store.push(0, 0., 0., 4., 0., .2, 10, 0., 0.)
        ly.data["primitives"] = store
        v.layers = [ly]
        feature = v._pick_measure_feature(3., .05)
        self.assertEqual(feature.center, (2., 0.))
        store.rec[0] = 2
        store.rec[1:5] = type(store.rec)("d", [1., 0., 1., 0.])
        store.rec[7] = -1.
        arc = primitive_feature(v, ly, 0)
        self.assertTrue(arc.approximate)
        self.assertAlmostEqual(distance(arc.boundary((0., 3.)), (0., 0.)), 1.1, delta=.0011)
        circle = primitive_feature(v, pad(0, 3.), 0)
        self.assertAlmostEqual(distance(*nearest_features(arc, circle)), .9, delta=.0011)


class CompareTests(unittest.TestCase):
    def compute(self, a, b, bbox=(-2., -2., 10., 5.), resolution=.01, tolerance=0):
        pairs, notes = pair_layers(a, b)
        return viewer()._compute_diff({"pairs": pairs, "notes": notes, "bbox": bbox,
                                      "resolution": resolution, "tolerance": tolerance,
                                      "namesA": [l.name for l in a], "namesB": [l.name for l in b]})

    def test_x2_bottom_and_legacy_name_pair(self):
        a, b = pad(0), pad(0)
        a.role = {"kind": "copper", "side": "bottom", "index": 4}
        b.role = {"kind": "copper", "side": "bottom", "index": None}
        self.assertEqual(len(pair_layers([a], [b])[0]), 1)
        self.assertEqual(self.compute([a], [b])["total"], 0)

    def test_difference_coordinates_and_missing_layer(self):
        a = pad(1., 2., dims=(.2,))
        a.role = {"kind": "copper", "side": "top"}
        b = pad(5., 2., dims=(.2,))
        b.role = {"kind": "copper", "side": "bottom"}
        result = self.compute([a], [b])
        self.assertEqual(result["total"], 2)
        for point in result["points"]:
            self.assertAlmostEqual(point["x"], 1. if point["side"] == "A" else 5., delta=.02)
            self.assertAlmostEqual(point["y"], 2., delta=.01)

    def test_declared_missing_apertures_native_matches_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSLAX24Y24*%%MOMM*%D10*X10000Y20000D03*M02*")
            data = parse_gerber(path)
            with patch("gerber_reader._native_backend", return_value=None):
                # Override avoids cache so the Python path is actually tested.
                python = parse_gerber(path, unit_override="mm")
            self.assertEqual(data["missing_apertures"], [10])
            self.assertEqual(python["missing_apertures"], [10])
            self.assertEqual(data["warnings"], python["warnings"])

    def test_cross_format_d_x_and_x2_same_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = {
                "legacy.gbl": "G71*G90*G54D10*X010000Y020000D03*X050000Y020000D03*M02*",
                "extended.gbl": "%FSLAX24Y24*%%MOMM*%%ADD10C,1*%D10*X10000Y20000D03*X50000Y20000D03*M02*",
                "x2.gbr": "%FSLAX24Y24*%%MOMM*%%TF.FileFunction,Copper,L4,Bot*%%ADD10C,1*%"
                          "D10*X10000Y20000D03*X50000Y20000D03*M02*",
            }
            layers = []
            for name, content in fixtures.items():
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="ascii") as f:
                    f.write(content)
                options = {"unit_override": "mm", "format_override": (2, 4),
                           "aperture_override": parse_aperture_table("D10 C 1")} if name.startswith("legacy") else {}
                data = parse_gerber(path, **options)
                data["_role"] = identify_layer(data)
                self.assertEqual(data.get("missing_apertures"), [])
                self.assertEqual(data["warnings"], [])
                layers.append(GerberLayer(name, data, "#fff"))
            for a in layers:
                for b in layers:
                    with self.subTest(a=a.name, b=b.name):
                        self.assertEqual(self.compute([a], [b])["total"], 0)

    def test_external_table_missing_and_cache_independence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("G71*D10*X010000Y020000D03*M02*")
            raw = parse_gerber(path)
            self.assertEqual(raw["missing_apertures"], [10])
            repaired = parse_gerber(path, format_override=(2, 4), unit_override="mm",
                                    aperture_override=parse_aperture_table("D10 C 1"))
            self.assertEqual(repaired["bbox"], [1., 2., 1., 2.])
            self.assertEqual(repaired["missing_apertures"], [])
            again = parse_gerber(path)
            self.assertEqual(again["bbox"], raw["bbox"])
            self.assertEqual(again["missing_apertures"], [10])
            self.assertEqual(again["apertures"], {})

    def test_explicit_parameters_override_declared_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "override.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("%FSTAX33Y33*%%MOIN*%%ADD10C,9*%G70*D10*X010000Y020000D03*M02*")
            data = parse_gerber(path, unit_override="mm", format_override=(2, 4),
                                zero_mode_override="L", aperture_override=parse_aperture_table("D10 C 1"))
            self.assertEqual(data["bbox"], [1., 2., 1., 2.])
            self.assertEqual(data["primitives"].rec[5], 1.)

    def test_no_tolerance_wrap_and_tile_consistency(self):
        image = Image.new("L", (7, 7), 0)
        image.putpixel((0, 3), 255)
        self.assertEqual(_dilate_mm(image, .1, .1).getpixel((6, 3)), 0)
        a, b = pad(0), pad(.02)
        with patch.object(gerber_diff, "TILE_SIZE", 512):
            large = self.compute([a], [b], bbox=(-2., -2., 2., 2.), tolerance=.03)
        with patch.object(gerber_diff, "TILE_SIZE", 32):
            small = self.compute([a], [b], bbox=(-2., -2., 2., 2.), tolerance=.03)
        self.assertEqual(large["total"], 0)
        self.assertEqual(small["total"], 0)

    def test_alignment_preserves_relative_offsets_and_is_idempotent(self):
        v = viewer()
        a, b, c = pad(0), pad(10), pad(20)
        a.group = "A"; b.group = c.group = "B"
        a.offset = [7., 3.]; b.offset = [2., 1.]; c.offset = [4., 2.]
        v.layers = [a, b, c]
        v._recompute_view_bbox = lambda: None
        v._schedule_render = lambda: None
        v._align_group_b_to_a()
        self.assertEqual([c.offset[k] - b.offset[k] for k in (0, 1)], [2., 1.])
        offsets = [list(l.offset) for l in v.layers]
        v._align_group_b_to_a()
        self.assertEqual(offsets, [l.offset for l in v.layers])
        self.assertAlmostEqual((b.bbox()[0] + c.bbox()[2]) / 2, 7.)

    def test_results_invalidate_only_for_comparison_changes(self):
        v = viewer()
        ly = pad(0); ly.group = "A"; v.layers = [ly]
        v._diff_source_signature = v._comparison_signature()
        v.diff_result = {"total": 1}; v.diff_points = [1]
        ly.color = "#f00"
        v._invalidate_changed_comparison()
        self.assertIsNotNone(v.diff_result)
        ly.offset[0] = 1.
        v._invalidate_changed_comparison()
        self.assertIsNone(v.diff_result)
        self.assertEqual(v.diff_points, [])
        self.assertTrue(v._diff_cancel.is_set())

    def test_aperture_table_validation_and_project_roundtrip(self):
        for text in ("D1 C 1", "D10 C -1", "D10 R 1", "D10 Q 1", "D10 P 1 3.5", "D10 C nan", "D10 C 1\nD10 C 2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_aperture_table(text)
        ly = pad(0)
        ly.parse_options = {"unit_override": "mm", "format_override": (2, 4),
                            "aperture_override": parse_aperture_table("D10 C 1")}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "project.egv.json")
            save(path, [ly])
            record = load(path)[0]
            self.assertEqual(record["parse_options"]["format_override"], [2, 4])
            self.assertEqual(record["parse_options"]["aperture_override"][10]["params"], [1.])

    def test_aperture_table_dialects_and_unit_conversion(self):
        # 形状全称 / SQUARE 单边长 / OCTAGON 正八边形
        t = parse_aperture_table("D10 CIRCLE 0.2\nD11 SQUARE 0.5\nD12 OBROUND 1.5 0.6\n"
                                 "D13 OCTAGON 1.0\nD14 RECTANGLE 1.0 0.5\nD15 POLYGON 1.0 6")
        self.assertEqual(t[10], {"shape": "C", "params": [0.2]})
        self.assertEqual(t[11], {"shape": "R", "params": [0.5, 0.5]})
        self.assertEqual(t[12], {"shape": "O", "params": [1.5, 0.6]})
        self.assertEqual(t[13], {"shape": "P", "params": [1.0, 8.0]})
        self.assertEqual(t[15], {"shape": "P", "params": [1.0, 6.0]})
        # * 与 ; 分隔条目, =/: 分隔字段, D 前缀可省, 注释与常见表头跳过
        t = parse_aperture_table("# comment\nD-CODE SHAPE SIZE\n10 C 0.2*D11=C,0.5;D12:O 1.5 0.6*\n// done")
        self.assertEqual(sorted(t), [10, 11, 12])
        self.assertEqual(t[11]["params"], [0.5])
        # UNITS 表头 / 数值后缀 -> 换算到目标单位
        t = parse_aperture_table("UNITS MILS\nD10 C 20\nD11 R 40 20", target_unit="mm")
        self.assertAlmostEqual(t[10]["params"][0], 0.508)
        self.assertAlmostEqual(t[11]["params"][0], 1.016)
        self.assertAlmostEqual(t[11]["params"][1], 0.508)
        t = parse_aperture_table("D10 C 0.01in\nD11 C 10mil", target_unit="mm")
        self.assertAlmostEqual(t[10]["params"][0], 0.254)
        self.assertAlmostEqual(t[11]["params"][0], 0.254)
        t = parse_aperture_table("UNITS: INCH\nD10 C 0.01", target_unit="inch")
        self.assertAlmostEqual(t[10]["params"][0], 0.01)
        # 不给目标单位时按原值 (工程文件回读语义, 不重复换算)
        self.assertEqual(parse_aperture_table("D10 C 0.508")[10]["params"], [0.508])
        # 未知方言仍然拒绝
        for text in ("D10 HEXAGON 1", "HELLO WORLD", "D10 C 0.2mm??", "D10 SQUARE 1 2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_aperture_table(text)

    def test_rs274d_dialect_table_repairs_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.gbr")
            with open(path, "w", encoding="ascii") as f:
                f.write("G70*G90*G54D10*X010000Y020000D03*X050000Y020000D03*M02*")
            table = parse_aperture_table("UNITS MILS\nD10 CIRCLE 1000", target_unit="inch")
            data = parse_gerber(path, unit_override="inch", format_override=(2, 4),
                                aperture_override=table)
            self.assertEqual(data["missing_apertures"], [])
            # bbox 统一输出 mm: 1in/2in/5in -> 25.4/50.8/127
            self.assertEqual(data["bbox"], [25.4, 50.8, 127.0, 50.8])


if __name__ == "__main__":
    unittest.main()
