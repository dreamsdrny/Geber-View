"""Manufacturing and comparison reports, independent of the GUI."""
import csv
import time
from collections import Counter


def manufacturing(layers):
    lines = ["Gerber / NC Drill 工程报告", "=" * 36, f"图层总数: {len(layers)}", ""]
    for ly in layers:
        data = ly.data or {}
        lines.append(f"[{ly.group or '普通'}] {ly.name}")
        lines.append(f"  类型: {ly.type_label()}  单位/格式: {ly.unit_label()}")
        lines.append(f"  比例: ×{ly.scale_factor:g}  偏移: {ly.offset} mm  镜像: {ly.mirror_x}")
        box = ly.bbox()
        if box:
            lines.append(f"  图幅: {box[2]-box[0]:.4f} × {box[3]-box[1]:.4f} mm")
        if ly.kind == "drill":
            holes = data.get("holes") or []
            lines.append(f"  孔/槽总数: {len(holes)}；槽孔: {sum(bool(h.get('is_slot')) for h in holes)}")
            counts = Counter((h.get("diameter_mm"), h.get("plating") or "unknown", bool(h.get("is_slot"))) for h in holes)
            for (diameter, plating, slot), count in sorted(counts.items(), key=lambda x: str(x[0])):
                lines.append(f"    {'槽' if slot else '孔'} 直径 {diameter if diameter is not None else '未知'} mm  {plating}: {count}")
        else:
            lines.append(f"  图元: {data.get('feature_count', 0)}  最小线宽: {data.get('min_line_width_mm')} mm")
        for warning in data.get("warnings") or []:
            lines.append("  警告: " + str(warning))
        for note in data.get("notes") or []:
            lines.append("  信息: " + str(note))
        lines.append("")
    return "\n".join(lines)


def write_diff_csv(path, result):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["配对图层", "差异侧", "X(mm)", "Y(mm)", "面积(mm²)", "像素数", "像素尺寸(mm)", "容差(mm)"])
        for point in result["points"]:
            writer.writerow([point.get("layer", ""), point["side"], point["x"], point["y"],
                             point["area"], point["cells"], result["cell_mm"][0], result["tolerance"]])


def _diff_summary(result):
    points = result.get("points") or []
    na = result.get("na")
    nb = result.get("nb")
    if na is None or nb is None:
        na = sum(p["side"] == "A" for p in points)
        nb = len(points) - na
    cw, chh = result.get("cell_mm", (0, 0))
    return {"total": result.get("total", len(points)), "na": na, "nb": nb,
            "namesA": result.get("namesA") or [], "namesB": result.get("namesB") or [],
            "cell": f"{cw:g}×{chh:g}", "tolerance": result.get("tolerance", 0),
            "notes": result.get("notes") or [],
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def _esc(text):
    # 打包时排除了标准库 html 模块, 这里自行转义
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_diff_html(path, result):
    """自包含 HTML 对比报告: 浏览器直接打开, 无需 Excel/管理员权限。"""
    s = _diff_summary(result)
    rows = []
    for i, p in enumerate(result.get("points") or [], 1):
        side = "A 多" if p["side"] == "A" else "B 多"
        cls = "a" if p["side"] == "A" else "b"
        rows.append(f"<tr><td>{i}</td><td>{_esc(p.get('layer', ''))}</td>"
                    f"<td class='{cls}'>{side}</td><td>{p['x']:.3f}</td><td>{p['y']:.3f}</td>"
                    f"<td>{p['area']}</td><td>{p['cells']}</td></tr>")
    notes = "".join(f"<li>{_esc(n)}</li>" for n in s["notes"])
    notes_html = f"<h2>配对说明</h2><ul>{notes}</ul>" if notes else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>A/B 对比差异报告</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#222}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:20px}}
table.meta{{border-collapse:collapse;margin:8px 0}}
table.meta td{{border:1px solid #ccc;padding:4px 12px}}
table.meta td:first-child{{background:#f5f5f5;font-weight:bold}}
table.diff{{border-collapse:collapse;width:100%;margin-top:8px}}
table.diff th,table.diff td{{border:1px solid #ccc;padding:4px 8px;text-align:center}}
table.diff th{{background:#f0f0f0}}
table.diff tr:nth-child(even){{background:#fafafa}}
td.a{{color:#c0008a;font-weight:bold}} td.b{{color:#0080a0;font-weight:bold}}
.ok{{color:#2e7d32;font-weight:bold}}
</style></head><body>
<h1>A/B 对比差异报告</h1>
<table class="meta">
<tr><td>生成时间</td><td>{s['time']}</td></tr>
<tr><td>A 组图层 ({len(s['namesA'])})</td><td>{_esc('、'.join(s['namesA']) or '无')}</td></tr>
<tr><td>B 组图层 ({len(s['namesB'])})</td><td>{_esc('、'.join(s['namesB']) or '无')}</td></tr>
<tr><td>像素尺寸</td><td>{s['cell']} mm</td></tr>
<tr><td>容差</td><td>{s['tolerance']:g} mm</td></tr>
<tr><td>差异总数</td><td>{s['total']} 处（A 多 {s['na']} / B 多 {s['nb']}）</td></tr>
</table>
{'<p class="ok">指定精度与容差下未发现差异。</p>' if not s['total'] else ''}
{notes_html}
<h2>差异明细（按面积从大到小）</h2>
<table class="diff">
<tr><th>#</th><th>配对图层</th><th>差异侧</th><th>X(mm)</th><th>Y(mm)</th><th>面积(mm²)</th><th>像素数</th></tr>
{''.join(rows)}
</table>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_diff_txt(path, result):
    s = _diff_summary(result)
    lines = ["A/B 对比差异报告", "=" * 36,
             f"生成时间: {s['time']}",
             f"A 组图层 ({len(s['namesA'])}): {'、'.join(s['namesA']) or '无'}",
             f"B 组图层 ({len(s['namesB'])}): {'、'.join(s['namesB']) or '无'}",
             f"像素尺寸: {s['cell']} mm  容差: {s['tolerance']:g} mm",
             f"差异总数: {s['total']} 处 (A多 {s['na']} / B多 {s['nb']})", ""]
    lines.extend("说明: " + str(n) for n in s["notes"])
    if s["notes"]:
        lines.append("")
    lines.append(f"{'#':>4}  {'配对图层':<24} {'差异侧':<6} {'X(mm)':>10} {'Y(mm)':>10} {'面积(mm²)':>12} {'像素数':>8}")
    for i, p in enumerate(result.get("points") or [], 1):
        lines.append(f"{i:>4}  {p.get('layer', ''):<24} {'A 多' if p['side'] == 'A' else 'B 多':<6} "
                     f"{p['x']:>10.3f} {p['y']:>10.3f} {p['area']:>12} {p['cells']:>8}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
