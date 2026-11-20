//! Strict experimental subset for the standalone viewer.
//! Reject anything that cannot be displayed without silently losing geometry.
use super::{trim, up, KIND_C, P};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Stroke {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
    pub diameter: f64,
    pub flash: bool,
}

#[derive(Debug)]
pub struct PreviewLayer {
    pub strokes: Vec<Stroke>,
}

fn ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|c| c.is_ascii_digit())
}

fn coordinate(text: &str) -> bool {
    let text = text
        .strip_prefix('-')
        .or_else(|| text.strip_prefix('+'))
        .unwrap_or(text);
    ascii_digits(text) && text.len() <= 9
}

fn command(raw: &[u8], end_seen: &mut bool, defined: &[i32]) -> Result<(), &'static str> {
    let upper = up(trim(raw));
    let s = std::str::from_utf8(&upper).map_err(|_| "命令不是 ASCII")?;
    if s.is_empty() {
        return Ok(());
    }
    if *end_seen {
        return Err("文件结束命令后存在数据");
    }
    if s.starts_with("G04") {
        return Ok(());
    }
    if s.starts_with("//") {
        return Err("暂不支持非 Gerber 注释格式");
    }
    if s == "M02" || s == "M2" {
        *end_seen = true;
        return Ok(());
    }
    let bytes = s.as_bytes();
    let mut i = 0;
    let mut seen = [false; 4];
    while i < bytes.len() {
        let letter = bytes[i];
        if !matches!(letter, b'X' | b'Y' | b'D' | b'G') {
            return Err("暂不支持的普通命令");
        }
        let slot = match letter {
            b'X' => 0,
            b'Y' => 1,
            b'D' => 2,
            _ => 3,
        };
        if seen[slot] {
            return Err("一条命令包含重复字段");
        }
        seen[slot] = true;
        i += 1;
        let start = i;
        if matches!(letter, b'X' | b'Y') && i < bytes.len() && matches!(bytes[i], b'+' | b'-') {
            i += 1;
        }
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        let value = &s[start..i];
        if !coordinate(value) || value.starts_with('+') {
            return Err("坐标或命令格式无效");
        }
        if letter == b'G' {
            let n = value.parse::<u32>().map_err(|_| "G 命令无效")?;
            if !matches!(n, 1 | 75 | 90) {
                return Err("暂不支持圆弧、区域或其他 G 命令");
            }
        } else if letter == b'D' {
            let n = value.parse::<i32>().map_err(|_| "D 命令无效")?;
            if n >= 10 && !defined.contains(&n) {
                return Err("引用了未定义的光圈");
            }
            if n >= 10 && (seen[0] || seen[1] || i != bytes.len()) {
                return Err("暂不支持光圈选择与坐标合并");
            }
            if n == 0 || (n > 3 && n < 10) {
                return Err("D 命令无效");
            }
        }
    }
    if seen[3] && (seen[0] || seen[1] || seen[2]) {
        return Err("暂不支持 G 命令与坐标或光圈合并");
    }
    Ok(())
}

fn extension(raw: &[u8], defined: &mut Vec<i32>) -> Result<(), &'static str> {
    let upper = up(trim(raw));
    let s = std::str::from_utf8(&upper).map_err(|_| "扩展命令不是 ASCII")?;
    if s.is_empty() {
        return Ok(());
    }
    if s.starts_with("FS") {
        let b = s.as_bytes();
        if b.len() != 10
            || b[2] != b'L'
            || b[3] != b'A'
            || b[4] != b'X'
            || b[7] != b'Y'
            || !b[5].is_ascii_digit()
            || !b[6].is_ascii_digit()
            || b[5] != b[8]
            || b[6] != b[9]
            || b[5] == b'0'
        {
            return Err("暂只支持相同 X/Y 格式的前导零省略绝对坐标");
        }
        return Ok(());
    }
    if s.starts_with("TF.FILEPOLARITY,") && s != "TF.FILEPOLARITY,POSITIVE" {
        return Err("暂不支持负极性文件");
    }
    if matches!(s, "MOMM" | "MOIN" | "LPD") {
        return Ok(());
    }
    if s.starts_with("TF.") {
        return Err("暂不支持文件属性，请使用旧版查看");
    }
    if s.starts_with("TA.") || s.starts_with("TO.") || s == "TD" || s.starts_with("TD.") {
        return Err("暂不支持网络/光圈属性，请使用旧版查看");
    }
    if let Some(rest) = s.strip_prefix("ADD") {
        let Some((code, diameter)) = rest.split_once("C,") else {
            return Err("暂只支持无孔圆形光圈");
        };
        if !ascii_digits(code) || code.len() > 9 {
            return Err("光圈编号无效");
        }
        let code = code.parse::<i32>().map_err(|_| "光圈编号无效")?;
        if diameter.len() > 30
            || !diameter.bytes().all(|c| c.is_ascii_digit() || c == b'.')
            || diameter.matches('.').count() > 1
        {
            return Err("光圈直径格式无效");
        }
        let value = diameter.parse::<f64>().map_err(|_| "光圈直径无效")?;
        if code < 10 || !value.is_finite() || value <= 0.0 || defined.contains(&code) {
            return Err("光圈直径或编号无效");
        }
        defined.push(code);
        return Ok(());
    }
    Err("包含暂不支持的扩展命令或极性")
}

/// Only circular flashes/linear strokes, with declared FS/MO and positive polarity.
pub fn parse_preview(data: &[u8]) -> Result<PreviewLayer, &'static str> {
    if data.len() > 128 * 1024 * 1024 {
        return Err("文件超过 128 MiB 安全上限");
    }
    let data = data.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(data);
    let mut i = 0;
    let mut end_seen = false;
    let mut defined = Vec::new();
    let mut fs_seen = false;
    let mut unit_seen = false;
    let mut geometry_seen = false;
    let mut operations = 0usize;
    let mut block_count = 0usize;
    while i < data.len() {
        while i < data.len() && data[i].is_ascii_whitespace() {
            i += 1;
        }
        if i == data.len() {
            break;
        }
        if end_seen {
            return Err("文件结束命令后存在数据");
        }
        if data[i] == b'%' {
            block_count += 1;
            if block_count > 1_000_000 {
                return Err("Gerber 扩展命令数量超过安全上限");
            }
            let len = data[i + 1..]
                .iter()
                .position(|&c| c == b'%')
                .ok_or("扩展命令未闭合")?;
            let block = &data[i + 1..i + 1 + len];
            if !block.ends_with(b"*") {
                return Err("扩展命令缺少结束符");
            }
            for part in block.split(|&c| c == b'*') {
                let upper = up(trim(part));
                if upper.starts_with(b"FS") {
                    if fs_seen || !defined.is_empty() || geometry_seen {
                        return Err("格式声明重复或顺序无效");
                    }
                    fs_seen = true;
                } else if upper.starts_with(b"MO") {
                    if unit_seen || !defined.is_empty() || geometry_seen {
                        return Err("单位声明重复或顺序无效");
                    }
                    unit_seen = true;
                } else if upper.starts_with(b"ADD") && (!fs_seen || !unit_seen) {
                    return Err("定义光圈前必须声明格式和单位");
                }
                if upper.starts_with(b"ADD") && geometry_seen {
                    return Err("暂不支持绘图过程中定义光圈");
                }
                extension(part, &mut defined)?;
            }
            i += len + 2;
        } else {
            let len = data[i..]
                .iter()
                .position(|&c| c == b'*')
                .ok_or("命令缺少结束符")?;
            let upper = up(trim(&data[i..i + len]));
            if !upper.starts_with(b"G04")
                && !upper.starts_with(b"//")
                && (upper.contains(&b'X') || upper.contains(&b'Y') || upper.starts_with(b"D"))
            {
                if !fs_seen || !unit_seen {
                    return Err("坐标前必须声明格式和单位");
                }
                geometry_seen = true;
                if upper.contains(&b'X') || upper.contains(&b'Y') {
                    operations += 1;
                    if operations > 2_000_000 {
                        return Err("坐标命令超过安全上限");
                    }
                }
            }
            command(&data[i..i + len], &mut end_seen, &defined)?;
            i += len + 1;
        }
    }
    if !end_seen {
        return Err("文件缺少 M02 结束命令");
    }
    if !fs_seen || !unit_seen {
        return Err("文件缺少格式或单位声明");
    }
    let mut p = P::new(data);
    if !p.run() {
        return Err("缺少格式/单位声明或存在不支持的命令");
    }
    if p.warnings.iter().any(|w| w.contains("整数位")) {
        return Err("坐标超出格式声明范围");
    }
    if p.rec.len() != p.n * 9 || p.n == 0 || p.n > 2_000_000 {
        return Err("图元为空或超过安全上限");
    }
    let mut strokes = Vec::with_capacity(p.n);
    for r in p.rec.chunks_exact(9) {
        let code = r[0] as i32;
        if code != 0 && code != 1 {
            return Err("暂不支持此图元或极性");
        }
        let aperture = p
            .aps
            .iter()
            .find(|a| a.code == r[6] as i32)
            .ok_or("未定义的光圈")?;
        if aperture.kind != KIND_C || r[5] <= 0.0 || !r[1..=5].iter().all(|v| v.is_finite()) {
            return Err("无效或不支持的光圈和坐标");
        }
        strokes.push(Stroke {
            x1: r[1],
            y1: r[2],
            x2: r[3],
            y2: r[4],
            diameter: r[5],
            flash: code == 1,
        });
    }
    Ok(PreviewLayer { strokes })
}

#[cfg(test)]
mod tests {
    use super::*;
    const OK: &[u8] = b"%FSLAX34Y34*%%MOMM*%%ADD10C,0.2*%D10*X10000Y10000D03*X20000Y10000D01*M02*";
    #[test]
    fn basic_geometry() {
        let l = parse_preview(OK).unwrap();
        assert_eq!(l.strokes.len(), 2);
        assert_eq!(
            (l.strokes[0].x1, l.strokes[0].y1, l.strokes[0].diameter),
            (1.0, 1.0, 0.2)
        );
        assert!(l.strokes[0].flash);
        assert_eq!((l.strokes[1].x2, l.strokes[1].y2), (2.0, 1.0));
    }
    #[test]
    fn dangerous_inputs_fail_closed() {
        for content in [
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*G02X10000Y10000I10000J0D01*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*G36*X10000Y10000D01*G37*M02*",
            "%FSLAX34Y34*%%MOMM*%%LPC*%%ADD10C,1*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10R,1X2*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1X0.2*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*X10000Y10000D03*",
            "%FSLAX34Y34*%%MOMM*%%ZZTEST*%%ADD10C,1*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%TF.FilePolarity,Negative*%%ADD10C,1*%D10*X10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*X+10000Y10000D03*M02*",
            "%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*X10000Y10000D03*%MOIN*%M02*",
        ] {
            assert!(parse_preview(content.as_bytes()).is_err(), "{content}");
        }
    }

    #[test]
    fn accepts_realistic_drill_stroke_stream() {
        let mut input = String::from("%FSLAX34Y34*%%MOMM*%%ADD10C,0.1*%%ADD11C,0.6*%D10*");
        for i in 0..30 {
            let x = i * 2000;
            input.push_str(&format!("X{x}Y0D02*X{}Y0D01*", x + 1500));
        }
        input.push_str("M02*");
        let layer = parse_preview(input.as_bytes()).unwrap();
        assert_eq!(layer.strokes.len(), 30);
        assert!((layer.strokes[0].x2 - 0.15).abs() < 1e-9);
        assert!((layer.strokes.last().unwrap().x2 - 5.95).abs() < 1e-9);
    }

    #[test]
    fn matches_reference_big_layer_geometry() {
        let input = include_bytes!("../../../tools/_corpus/15_big.gbr");
        let layer = parse_preview(input).unwrap();
        assert_eq!(layer.strokes.len(), 15_500);
        let mut min_x = f64::INFINITY;
        let mut min_y = f64::INFINITY;
        let mut max_x = f64::NEG_INFINITY;
        let mut max_y = f64::NEG_INFINITY;
        for s in &layer.strokes {
            let r = s.diameter / 2.0;
            min_x = min_x.min(s.x1.min(s.x2) - r);
            min_y = min_y.min(s.y1.min(s.y2) - r);
            max_x = max_x.max(s.x1.max(s.x2) + r);
            max_y = max_y.max(s.y1.max(s.y2) + r);
        }
        for (actual, expected) in [(min_x, -0.3), (min_y, -0.3), (max_x, 60.0), (max_y, 49.05)] {
            assert!((actual - expected).abs() < 1e-8, "{actual} != {expected}");
        }
    }
}
