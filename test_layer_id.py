"""图层识别与 A/B 配对回归 (真实 CAM 命名约定)。

锁定 P0 修复: .art 兜底误判、阻焊顶底平票、内层层号提取、短名约定、
.txt 内容判钻孔、via 钻孔扩展名归一、内层 1v1 歧义提示。
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gerber_layer import identify_layer
from gerber_diff import pair_layers, layer_key


def role(name, **data):
    d = {"file": name}
    d.update(data)
    return identify_layer(d)


class ArtworkIdentification(unittest.TestCase):
    def test_allegro_art_stack(self):
        cases = {
            "TOP.art": ("copper", "top", 1),
            "BOTTOM.art": ("copper", "bottom", None),
            "BOT.art": ("copper", "bottom", None),
            "L2_GND.art": ("copper", "inner", 2),
            "L3_SIG.art": ("copper", "inner", 3),
            "L7_GND.art": ("copper", "inner", 7),
            "SILK_TOP.art": ("silkscreen", "top", None),
            "SILK_BOT.art": ("silkscreen", "bottom", None),
            "SMASK_TOP.art": ("soldermask", "top", None),
            "SMASK_BOT.art": ("soldermask", "bottom", None),
            "PMASK_TOP.art": ("paste", "top", None),
            "PMASK_BOT.art": ("paste", "bottom", None),
            "OUTLINE.art": ("profile", "", None),
            "FAB_DRAWING.art": ("other", "", None),
            "PANEL.art": ("other", "", None),
            "ASSEMBLY.art": ("other", "", None),
        }
        for name, (kind, side, index) in cases.items():
            with self.subTest(name=name):
                r = role(name)
                self.assertEqual(r["kind"], kind)
                self.assertEqual(r["side"], side)
                self.assertEqual(r["index"], index)

    def test_numbered_artwork_inner(self):
        r = role("art04.art")
        self.assertEqual((r["kind"], r["side"], r["index"]), ("copper", "inner", 4))


class SoldermaskSide(unittest.TestCase):
    def test_bottom_mask_not_hijacked_by_top(self):
        for name in ("SMASK_BOT.art", "soldermask_bottom.gbr", "mask_bot.gbr",
                     "board-solder-mask-bottom.gbr", "SMASK_BOTTOM.art"):
            with self.subTest(name=name):
                r = role(name)
                self.assertEqual(r["kind"], "soldermask")
                self.assertEqual(r["side"], "bottom")

    def test_top_mask(self):
        for name in ("SMASK_TOP.art", "soldermask_top.gts", "mask_top.gbr"):
            with self.subTest(name=name):
                r = role(name)
                self.assertEqual((r["kind"], r["side"]), ("soldermask", "top"))

    def test_sideless_mask_does_not_guess(self):
        r = role("soldermask.gbr")
        self.assertEqual(r["kind"], "soldermask")
        self.assertEqual(r["side"], "")


class ShortCamNames(unittest.TestCase):
    def test_inner_short_names_carry_index(self):
        for n in (2, 3, 7):
            r = role(f"in{n}")
            self.assertEqual((r["kind"], r["side"], r["index"]), ("copper", "inner", n))

    def test_outline_short(self):
        for name in ("ol1", "ol"):
            self.assertEqual(role(name)["kind"], "profile")

    def test_ambiguous_short_names_are_typed(self):
        expect = {"cm": ("copper", "top"), "cs": ("copper", "bottom"),
                  "sm": ("soldermask", "top"), "ss": ("silkscreen", "top"),
                  "sss": ("silkscreen", "bottom")}
        for name, (kind, side) in expect.items():
            with self.subTest(name=name):
                r = role(name)
                self.assertEqual((r["kind"], r["side"]), (kind, side))


class InnerIndexExtraction(unittest.TestCase):
    def test_inner_names_get_distinct_indices(self):
        self.assertEqual(role("Inner2.gbr")["index"], 2)
        self.assertEqual(role("Inner4.gbr")["index"], 4)
        self.assertEqual(role("inr3.gbr")["index"], 3)
        self.assertNotEqual(layer_key(SimpleNamespace(
            role=role("Inner2.gbr"), kind="gerber", data={"file": "Inner2.gbr"}, name="Inner2.gbr")),
            layer_key(SimpleNamespace(
                role=role("Inner4.gbr"), kind="gerber", data={"file": "Inner4.gbr"}, name="Inner4.gbr")))


class DrillByContent(unittest.TestCase):
    def test_txt_gerber_not_drill(self):
        r = identify_layer({"file": "measure.txt", "kind": "gerber"})
        self.assertNotEqual(r["kind"], "drill")

    def test_txt_drill_is_drill(self):
        r = identify_layer({"file": "holes.txt", "kind": "drill", "plating": "PTH"})
        self.assertEqual(r["kind"], "drill")
        self.assertEqual(r["plated"], True)

    def test_via_drill_kept_from_name(self):
        r = identify_layer({"file": "bvh1-2.drl", "kind": "drill"})
        self.assertEqual(r["kind"], "drill")
        self.assertTrue(r.get("via"))


class X2Priority(unittest.TestCase):
    def test_x2_bottom_copper(self):
        r = identify_layer({"file": "x2.gbr", "file_function": "Copper,L4,Bot"})
        self.assertEqual((r["kind"], r["side"], r["index"]), ("copper", "bottom", 4))

    def test_legacy_extensions_unchanged(self):
        self.assertEqual((role("top.gtl")["kind"], role("top.gtl")["side"]), ("copper", "top"))
        self.assertEqual((role("bottom.gbl")["kind"], role("bottom.gbl")["side"]), ("copper", "bottom"))


def _layer(name, r, kind="gerber"):
    return SimpleNamespace(name=name, role=r, kind=kind, data={"file": name})


class PairingFixes(unittest.TestCase):
    def test_via_drill_extension_normalized(self):
        a = _layer("bvh1-2.drl", {"kind": "drill", "via": True, "plated": True}, kind="drill")
        b = _layer("bvh1-2.nc", {"kind": "drill", "via": True, "plated": True}, kind="drill")
        pairs, _ = pair_layers([a], [b])
        self.assertEqual(len(pairs), 1)
        self.assertIsNotNone(pairs[0][1])
        self.assertIsNotNone(pairs[0][2])

    def test_inner_missing_index_warns(self):
        a = _layer("innerA.gbr", {"kind": "copper", "side": "inner", "index": None})
        b = _layer("innerB.gbr", {"kind": "copper", "side": "inner", "index": None})
        _, notes = pair_layers([a], [b])
        self.assertTrue(any("层号缺失" in n for n in notes))

    def test_inner_matching_index_no_warn(self):
        a = _layer("L2_GND.art", role("L2_GND.art"))
        b = _layer("in2", role("in2"))
        pairs, notes = pair_layers([a], [b])
        self.assertEqual(len(pairs), 1)
        self.assertIsNotNone(pairs[0][2])
        self.assertFalse(any("层号缺失" in n for n in notes))

    def test_real_stack_pairs(self):
        a = [_layer(n, role(n)) for n in
             ("TOP.art", "BOTTOM.art", "L2_GND.art", "SILK_BOT.art", "SMASK_BOT.art", "OUTLINE.art")]
        b = [_layer(n, role(n)) for n in ("cm", "cs", "in2", "sss", "sm", "ol1")]
        pairs, _ = pair_layers(a, b)
        both = sum(1 for _, la, lb in pairs if la and lb)
        # 顶/底铜、内层2、底丝印、板框 应双边配上 (sm 猜顶层, 底阻焊缺 B)
        self.assertGreaterEqual(both, 5)


class ProjectRoleOverride(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        from gerber_viewer import GerberLayer
        from gerber_project import save, load
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "cm")
            with open(src, "w") as f:
                f.write("%FSLAX26Y26*%%MOIN*%%ADD10C,0.01*%D10*X100000Y100000D03*M02*")
            data = {"file": src, "kind": "gerber", "_role": {"kind": "copper", "side": "top"}}
            ly = GerberLayer("cm", data, "#fff")
            ly.role_override = {"kind": "copper", "side": "bottom", "index": None}
            proj = os.path.join(tmp, "p.egv.json")
            save(proj, [ly])
            rec = load(proj)[0]
            self.assertEqual(rec["role_override"],
                             {"kind": "copper", "side": "bottom", "index": None})

    def test_invalid_override_rejected(self):
        import json
        import tempfile
        from gerber_project import load
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "bad.egv.json")
            bad = {"version": 1, "layers": [{
                "path": "cm", "name": "cm", "group": "", "color": "#fff",
                "visible": True, "scale": 1, "offset": [0, 0], "mirror_x": False,
                "role_override": {"kind": "bogus", "side": "top", "index": None},
                "parse_options": {}}]}
            with open(proj, "w", encoding="utf-8") as f:
                json.dump(bad, f)
            with self.assertRaises(ValueError):
                load(proj)


if __name__ == "__main__":
    unittest.main()