"""Manufacturing and comparison reports, independent of the GUI."""
import csv
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
            lines.append("  提示: " + str(warning))
        lines.append("")
    return "\n".join(lines)


def write_diff_csv(path, result):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["配对图层", "差异侧", "X(mm)", "Y(mm)", "面积(mm²)", "像素数", "像素尺寸(mm)", "容差(mm)"])
        for point in result["points"]:
            writer.writerow([point.get("layer", ""), point["side"], point["x"], point["y"],
                             point["area"], point["cells"], result["cell_mm"][0], result["tolerance"]])
