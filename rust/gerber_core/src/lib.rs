//! Gerber 解析热路径 —— Rust 原生扩展
//!
//! 只覆盖"能确定性复刻 Python 语义"的子集; 一旦遇到不支持的结构
//! (光圈宏 %AM / 拼版 %SR / 区域 G36-G37 / 单象限弧 G74 / 圆弧 G02-G03 /
//!  X2 属性 %TF-%TA-%TO-%TD / 未声明单位) 就返回 None,
//! Python 端整体回退到原解析器 —— 正确性由"要么逐字节相同, 要么不用"保证。

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};

const PRIM_DRAW: f64 = 0.0;
const PRIM_FLASH: f64 = 1.0;
const PRIM_ARC: f64 = 2.0;
const PRIM_REGION: f64 = 3.0;
const PRIM_ERROR: f64 = 4.0;
const PRIM_CLEAR: f64 = 8.0;
const PRIM_CW: f64 = 16.0;
const INCH_TO_MM: f64 = 25.4;

const KIND_C: u8 = 0;
const KIND_R: u8 = 1;
const KIND_O: u8 = 2;
const KIND_P: u8 = 3;

#[derive(Clone)]
struct Ap {
    code: i32,
    raw: String,
    kind: u8,
    p0: f64,
    p1: f64,
}

struct P<'a> {
    data: &'a [u8],
    // ---- 状态 ----
    inch: bool,
    unit_src: &'static str,
    uf: f64,
    int_d: i32,
    dec_d: i32,
    pow10: f64,
    zero_t: bool,
    incr: bool,
    fs_seen: bool,
    scale: [f64; 2],
    off: [f64; 2],
    mirror: [bool; 2],
    rot: i32,
    cur_ap: Option<i32>,
    rx: f64,
    ry: f64,
    interp: u8,
    cur_op: u8,
    in_region: bool,
    region: Vec<f64>,
    // 输出: 圆弧列表 [(x1,y1,x2,y2,i,j,cw)] 与区域点集 [(记录序号, [x,y,...])]
    // 圆弧的包围盒采样故意留给 Python (_arc_sample 用 hypot/atan2/cos,
    // 跨语言末位可能差 1 ULP, 交给 Python 才能保证位级一致)
    arcs: Vec<[f64; 7]>,
    regions_out: Vec<(usize, Vec<f64>)>,
    negative: bool,
    // ---- 输出 ----
    rec: Vec<f64>,
    n: usize,
    feat: usize,
    minw: Option<f64>,
    bbox: [Option<f64>; 4],
    span: [Option<f64>; 4],
    aps: Vec<Ap>,
    ap_use: Vec<(i32, u64)>,
    warnings: Vec<&'static str>,
}

fn is_ws(b: u8) -> bool {
    b == b' ' || b == b'\t' || b == b'\r' || b == b'\n'
}

fn trim(s: &[u8]) -> &[u8] {
    let mut a = 0;
    let mut b = s.len();
    while a < b && is_ws(s[a]) {
        a += 1;
    }
    while b > a && is_ws(s[b - 1]) {
        b -= 1;
    }
    &s[a..b]
}

fn up(s: &[u8]) -> Vec<u8> {
    s.iter().map(|c| c.to_ascii_uppercase()).collect()
}

/// 解析十进制整数 (可带前导 +/-, 仅数字)
fn parse_int(s: &[u8]) -> Option<i64> {
    if s.is_empty() {
        return None;
    }
    let (neg, d) = if s[0] == b'-' {
        (true, &s[1..])
    } else if s[0] == b'+' {
        (false, &s[1..])
    } else {
        (false, s)
    };
    if d.is_empty() {
        return None;
    }
    let mut v: i64 = 0;
    for &c in d {
        if !c.is_ascii_digit() {
            return None;
        }
        v = v.checked_mul(10)?.checked_add((c - b'0') as i64)?;
    }
    Some(if neg { -v } else { v })
}

/// 扫描所有浮点数 (等价 re.findall(r"[-+]?\d*\.?\d+"))
fn scan_nums(s: &[u8]) -> Vec<f64> {
    let mut out = Vec::new();
    let mut i = 0;
    let n = s.len();
    while i < n {
        let start = i;
        let mut j = i;
        if j < n && (s[j] == b'+' || s[j] == b'-') {
            j += 1;
        }
        let mut digits = 0;
        while j < n && s[j].is_ascii_digit() {
            j += 1;
            digits += 1;
        }
        if j < n && s[j] == b'.' {
            j += 1;
            while j < n && s[j].is_ascii_digit() {
                j += 1;
                digits += 1;
            }
        }
        if digits == 0 {
            i = start + 1;
            continue;
        }
        if let Ok(txt) = std::str::from_utf8(&s[start..j]) {
            if let Ok(v) = txt.parse::<f64>() {
                out.push(v);
            }
        }
        i = j;
    }
    out
}

impl<'a> P<'a> {
    fn new(data: &'a [u8]) -> Self {
        P {
            data,
            inch: false,
            unit_src: "",
            uf: 1.0,
            int_d: 3,
            dec_d: 3,
            pow10: 1000.0,
            zero_t: false,
            incr: false,
            fs_seen: false,
            scale: [1.0, 1.0],
            off: [0.0, 0.0],
            mirror: [false, false],
            rot: 0,
            cur_ap: None,
            rx: 0.0,
            ry: 0.0,
            interp: 1,
            cur_op: 1,
            in_region: false,
            region: Vec::new(),
            arcs: Vec::new(),
            regions_out: Vec::new(),
            negative: false,
            rec: Vec::new(),
            n: 0,
            feat: 0,
            minw: None,
            bbox: [None; 4],
            span: [None; 4],
            aps: Vec::new(),
            ap_use: Vec::new(),
            warnings: Vec::new(),
        }
    }

    // ---------- 坐标换算 (与 _coord_value 位级相同) ----------
    fn coord(&self, s: &[u8]) -> Option<f64> {
        let (neg, dig) = if !s.is_empty() && s[0] == b'-' {
            (true, &s[1..])
        } else {
            (false, s)
        };
        if dig.is_empty() {
            return None;
        }
        let buf: Vec<u8>;
        let d: &[u8] = if self.zero_t {
            let total = (self.int_d + self.dec_d) as usize;
            buf = {
                let mut v = dig.to_vec();
                while v.len() < total {
                    v.push(b'0');
                }
                v.truncate(total);
                v
            };
            &buf
        } else {
            dig
        };
        let v = parse_int(d)? as f64 / self.pow10;
        Some(if neg { -v } else { v })
    }

    fn pt(&self, xr: f64, yr: f64) -> (f64, f64) {
        let mut x = xr * self.uf * self.scale[0];
        let mut y = yr * self.uf * self.scale[1];
        if self.mirror[0] {
            x = -x;
        }
        if self.mirror[1] {
            y = -y;
        }
        if self.rot != 0 {
            let r = (self.rot as f64).to_radians();
            let (cs, sn) = (r.cos(), r.sin());
            let nx = x * cs - y * sn;
            let ny = x * sn + y * cs;
            x = nx;
            y = ny;
        }
        (x + self.off[0] * self.uf, y + self.off[1] * self.uf)
    }

    fn apw(&self, code: i32) -> Option<f64> {
        let ap = self.aps.iter().find(|a| a.code == code)?;
        let w = match ap.kind {
            KIND_R | KIND_O => ap.p0.min(ap.p1),
            _ => ap.p0,
        };
        Some(w * self.uf * ((self.scale[0] + self.scale[1]) / 2.0))
    }

    fn bump(&mut self, code: i32) {
        for e in self.ap_use.iter_mut() {
            if e.0 == code {
                e.1 += 1;
                return;
            }
        }
        self.ap_use.push((code, 1));
    }

    fn bbox_add(&mut self, x: f64, y: f64) {
        let b = &mut self.bbox;
        b[0] = Some(match b[0] {
            None => x,
            Some(v) => v.min(x),
        });
        b[1] = Some(match b[1] {
            None => y,
            Some(v) => v.min(y),
        });
        b[2] = Some(match b[2] {
            None => x,
            Some(v) => v.max(x),
        });
        b[3] = Some(match b[3] {
            None => y,
            Some(v) => v.max(y),
        });
    }

    fn track(&mut self, x: f64, y: f64) {
        let s = &mut self.span;
        s[0] = Some(match s[0] {
            None => x,
            Some(v) => v.min(x),
        });
        s[2] = Some(match s[2] {
            None => x,
            Some(v) => v.max(x),
        });
        s[1] = Some(match s[1] {
            None => y,
            Some(v) => v.min(y),
        });
        s[3] = Some(match s[3] {
            None => y,
            Some(v) => v.max(y),
        });
    }

    fn push_rec(
        &mut self,
        code: f64,
        x1: f64,
        y1: f64,
        x2: f64,
        y2: f64,
        w: f64,
        ap: f64,
        i: f64,
        j: f64,
    ) {
        self.rec
            .extend_from_slice(&[code, x1, y1, x2, y2, w, ap, i, j]);
        self.n += 1;
        self.feat += 1;
    }

    // ---------- 扩展命令块 ----------
    fn ext_block(&mut self, block: &[u8]) -> bool {
        let mut i = 0;
        let n = block.len();
        loop {
            let j = match block[i..].iter().position(|&c| c == b'*') {
                Some(p) => i + p,
                None => n,
            };
            let sub = trim(&block[i..j]);
            if !sub.is_empty() && !self.ext_one(sub) {
                return false;
            }
            if j >= n {
                break;
            }
            i = j + 1;
            if i > n {
                break;
            }
        }
        true
    }

    fn ext_one(&mut self, s: &[u8]) -> bool {
        let u = up(s);
        // ---- 不支持 -> 整体回退 ----
        if u.starts_with(b"AM")
            || u.starts_with(b"SR")
            || u.starts_with(b"AS")
            || u.starts_with(b"TF")
            || u.starts_with(b"TA")
            || u.starts_with(b"TO")
            || u.starts_with(b"TD")
        {
            return false;
        }
        if u.starts_with(b"FS") {
            let mut k = 2;
            if k < u.len() && (u[k] == b'L' || u[k] == b'T') {
                self.zero_t = u[k] == b'T';
                k += 1;
            }
            if k < u.len() && (u[k] == b'A' || u[k] == b'I') {
                if u[k] == b'I' {
                    self.incr = true;
                }
                k += 1;
            }
            if k >= u.len() || u[k] != b'X' {
                return false;
            }
            k += 1;
            if k + 1 >= u.len() || !u[k].is_ascii_digit() || !u[k + 1].is_ascii_digit() {
                return false;
            }
            self.int_d = (u[k] - b'0') as i32;
            self.dec_d = (u[k + 1] - b'0') as i32;
            self.pow10 = 10f64.powi(self.dec_d);
            self.fs_seen = true;
            return true;
        }
        if u.starts_with(b"MO") {
            let rest = trim(&u[2..]);
            if rest.starts_with(b"IN") {
                self.inch = true;
                self.uf = INCH_TO_MM;
                self.unit_src = "declared(%MO)";
            } else if rest.starts_with(b"MM") {
                self.inch = false;
                self.uf = 1.0;
                self.unit_src = "declared(%MO)";
            }
            return true;
        }
        if u.starts_with(b"ADD") || u.starts_with(b"AD") {
            return self.add(&u, s);
        }
        if u.starts_with(b"LP") {
            self.negative = u.starts_with(b"LPC");
            return true;
        }
        if u.starts_with(b"OF") {
            let nums = scan_nums(s.get(2..).unwrap_or(&[]));
            let a = nums.first().copied().unwrap_or(0.0);
            let b = nums.get(1).copied().unwrap_or(a);
            self.off = [a, b];
            return true;
        }
        if u.starts_with(b"SF") {
            let nums = scan_nums(s.get(2..).unwrap_or(&[]));
            let a = nums.first().copied().unwrap_or(1.0);
            let b = nums.get(1).copied().unwrap_or(a);
            self.scale = [
                if a != 0.0 { a } else { 1.0 },
                if b != 0.0 {
                    b
                } else if a != 0.0 {
                    a
                } else {
                    1.0
                },
            ];
            return true;
        }
        if u.starts_with(b"MI") {
            let digits: Vec<u8> = u[2..]
                .iter()
                .copied()
                .filter(|c| c.is_ascii_digit())
                .collect();
            self.mirror = [
                digits.first() == Some(&b'1'),
                digits.get(1) == Some(&b'1'),
            ];
            return true;
        }
        if u.starts_with(b"IR") {
            let nums = scan_nums(&u[2..]);
            let v = nums.first().copied().unwrap_or(0.0);
            self.rot = ((v as i32) % 360 + 360) % 360;
            return true;
        }
        true
    }

    fn add(&mut self, u: &[u8], s: &[u8]) -> bool {
        let mut k = if u.starts_with(b"ADD") { 3 } else { 2 };
        while k < u.len() && u[k] == b'D' {
            k += 1;
        }
        let st = k;
        while k < u.len() && u[k].is_ascii_digit() {
            k += 1;
        }
        let code = match parse_int(&u[st..k]) {
            Some(v) => v as i32,
            None => return false,
        };
        while k < u.len() && (is_ws(u[k]) || u[k] == b',') {
            k += 1;
        }
        let sh_st = k;
        while k < u.len() && (u[k].is_ascii_alphabetic() || u[k] == b'_' || u[k] == b'$') {
            k += 1;
        }
        let shape = &u[sh_st..k];
        let kind = match shape {
            b"C" => KIND_C,
            b"R" => KIND_R,
            b"O" => KIND_O,
            b"P" => KIND_P,
            _ => return false,
        };
        while k < u.len() && (is_ws(u[k]) || u[k] == b',') {
            k += 1;
        }
        let nums = scan_nums(&u[k..]);
        if nums.is_empty() {
            return false;
        }
        let p0 = nums[0];
        let p1 = nums.get(1).copied().unwrap_or(0.0);
        let raw = String::from_utf8_lossy(s).into_owned();
        self.aps.push(Ap {
            code,
            raw,
            kind,
            p0,
            p1,
        });
        true
    }

    // ---------- 普通命令 ----------
    fn cmd(&mut self, raw: &[u8]) -> bool {
        let s = trim(raw);
        if s.is_empty() {
            return true;
        }
        let u = up(s);
        if u.starts_with(b"G04")
            || u.starts_with(b"G4")
            || u.starts_with(b"//")
            || u.starts_with(b"M")
        {
            return true;
        }
        let mut new_ap: Option<i32> = None;
        let mut new_op: Option<u8> = None;
        let mut k = 0;
        while k < u.len() {
            let c = u[k];
            if c == b'G' || c == b'D' {
                let mut q = k + 1;
                while q < u.len() && u[q] == b'0' {
                    q += 1;
                }
                let st = q;
                while q < u.len() && u[q].is_ascii_digit() {
                    q += 1;
                }
                if q > st {
                    let v = parse_int(&u[st..q]).unwrap_or(-1) as i32;
                    if c == b'G' {
                        match v {
                            0 | 1 => self.interp = 1,
                            2 => self.interp = 2,
                            3 => self.interp = 3,
                            36 => {
                                self.in_region = true;
                                self.region.clear();
                            }
                            37 => self.close_region(),
                            74 => return false, // 单象限圆弧 (I/J 绝对值): 交给 Python
                            70 => {
                                self.inch = true;
                                self.uf = INCH_TO_MM;
                                self.unit_src = "declared(G70)";
                            }
                            71 => {
                                self.inch = false;
                                self.uf = 1.0;
                                self.unit_src = "declared(G71)";
                            }
                            90 => self.incr = false,
                            91 => self.incr = true,
                            _ => {}
                        }
                    } else if v >= 10 {
                        new_ap = Some(v);
                    } else if (1..=3).contains(&v) {
                        new_op = Some(v as u8);
                    }
                }
                k = if q > k { q } else { k + 1 };
            } else {
                k += 1;
            }
        }
        if let Some(a) = new_ap {
            self.cur_ap = Some(a);
        }
        if let Some(o) = new_op {
            self.cur_op = o;
        }
        let mut mx: Option<&[u8]> = None;
        let mut my: Option<&[u8]> = None;
        let mut mi: Option<&[u8]> = None;
        let mut mj: Option<&[u8]> = None;
        let mut k = 0;
        while k < u.len() {
            let c = u[k];
            if c == b'X' || c == b'Y' || c == b'I' || c == b'J' {
                let st = k + 1;
                let mut q = st;
                if q < u.len() && (u[q] == b'-' || u[q] == b'+') {
                    q += 1;
                }
                while q < u.len() && u[q].is_ascii_digit() {
                    q += 1;
                }
                if q < u.len() && u[q] == b'.' {
                    return false; // 带小数点 -> 交给 Python
                }
                let has_digit = q > st && !(q == st + 1 && (u[st] == b'-' || u[st] == b'+'));
                if !has_digit {
                    k = if q > k { q } else { k + 1 };
                    continue;
                }
                let v = &u[st..q];
                match c {
                    b'X' => {
                        if mx.is_none() {
                            mx = Some(v)
                        }
                    }
                    b'Y' => {
                        if my.is_none() {
                            my = Some(v)
                        }
                    }
                    b'I' => {
                        if mi.is_none() {
                            mi = Some(v)
                        }
                    }
                    _ => {
                        if mj.is_none() {
                            mj = Some(v)
                        }
                    }
                }
                k = q;
            } else {
                k += 1;
            }
        }
        if mx.is_none() && my.is_none() && mi.is_none() && mj.is_none() {
            return true;
        }
        let mut x_raw = match mx {
            Some(v) => match self.coord(v) {
                Some(x) => Some(x),
                None => return false,
            },
            None => None,
        };
        let mut y_raw = match my {
            Some(v) => match self.coord(v) {
                Some(x) => Some(x),
                None => return false,
            },
            None => None,
        };
        if self.incr {
            if let Some(v) = x_raw.as_mut() {
                *v += self.rx;
            }
            if let Some(v) = y_raw.as_mut() {
                *v += self.ry;
            }
        }
        let nx_raw = x_raw.unwrap_or(self.rx);
        let ny_raw = y_raw.unwrap_or(self.ry);
        self.track(nx_raw, ny_raw);
        let (nx, ny) = self.pt(nx_raw, ny_raw);
        let (cx, cy) = self.pt(self.rx, self.ry);
        let op = self.cur_op;
        if self.in_region {
            // 区域内只收集顶点 (与 Python 快速路径一致: 不看 op)
            self.region.push(nx);
            self.region.push(ny);
            self.rx = nx_raw;
            self.ry = ny_raw;
            return true;
        }
        if op == 3 {
            self.do_flash(nx, ny);
        } else if op == 1 {
            if self.interp == 1 {
                self.do_draw(cx, cy, nx, ny);
            } else {
                let i_raw = match mi {
                    Some(v) => match self.coord(v) {
                        Some(x) => x,
                        None => return false,
                    },
                    None => 0.0,
                };
                let j_raw = match mj {
                    Some(v) => match self.coord(v) {
                        Some(x) => x,
                        None => return false,
                    },
                    None => 0.0,
                };
                self.do_arc(cx, cy, nx, ny, i_raw, j_raw);
            }
        }
        self.rx = nx_raw;
        self.ry = ny_raw;
        true
    }

    fn vec(&self, dx_raw: f64, dy_raw: f64) -> (f64, f64) {
        let mut x = dx_raw * self.uf * self.scale[0];
        let mut y = dy_raw * self.uf * self.scale[1];
        if self.mirror[0] {
            x = -x;
        }
        if self.mirror[1] {
            y = -y;
        }
        if self.rot != 0 {
            let r = (self.rot as f64).to_radians();
            let (cs, sn) = (r.cos(), r.sin());
            let (nx, ny) = (x * cs - y * sn, x * sn + y * cs);
            x = nx;
            y = ny;
        }
        (x, y)
    }

    fn close_region(&mut self) {
        self.in_region = false;
        let pts = std::mem::take(&mut self.region);
        let cnt = pts.len() / 2;
        if cnt < 2 {
            return;
        }
        let (mut minx, mut miny) = (f64::INFINITY, f64::INFINITY);
        let (mut maxx, mut maxy) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
        for k in 0..cnt {
            let (x, y) = (pts[2 * k], pts[2 * k + 1]);
            if x < minx {
                minx = x;
            }
            if x > maxx {
                maxx = x;
            }
            if y < miny {
                miny = y;
            }
            if y > maxy {
                maxy = y;
            }
        }
        let code = PRIM_REGION + if self.negative { PRIM_CLEAR } else { 0.0 };
        self.push_rec(code, minx, miny, maxx, maxy, 0.0, 0.0, 0.0, 0.0);
        let idx = self.n - 1;
        for k in 0..cnt {
            self.bbox_add(pts[2 * k], pts[2 * k + 1]);
        }
        self.regions_out.push((idx, pts));
    }

    fn do_arc(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, i_raw: f64, j_raw: f64) {
        let (im, jm) = self.vec(i_raw, j_raw);
        let mut cw = self.interp == 2;
        if self.mirror[0] != self.mirror[1] {
            cw = !cw; // 单轴镜像后走向反转
        }
        let ap = self.cur_ap;
        let w = match ap {
            Some(a) => self.apw(a).unwrap_or(0.0),
            None => 0.0,
        };
        let mut code = PRIM_ARC + if self.negative { PRIM_CLEAR } else { 0.0 };
        if cw {
            code += PRIM_CW;
        }
        self.push_rec(code, x1, y1, x2, y2, w, ap.unwrap_or(0) as f64, im, jm);
        if w != 0.0 && (self.minw.is_none() || w < self.minw.unwrap()) {
            self.minw = Some(w);
        }
        if let Some(a) = ap {
            self.bump(a);
        }
        self.bbox_add(x1, y1);
        self.bbox_add(x2, y2);
        self.arcs
            .push([x1, y1, x2, y2, im, jm, if cw { 1.0 } else { 0.0 }]);
    }

    fn do_draw(&mut self, x1: f64, y1: f64, x2: f64, y2: f64) {
        let ap = self.cur_ap;
        let w = match ap {
            Some(a) => self.apw(a).unwrap_or(0.0),
            None => 0.0,
        };
        let code = PRIM_DRAW + if self.negative { PRIM_CLEAR } else { 0.0 };
        self.push_rec(code, x1, y1, x2, y2, w, ap.unwrap_or(0) as f64, 0.0, 0.0);
        if w != 0.0 && (self.minw.is_none() || w < self.minw.unwrap()) {
            self.minw = Some(w);
        }
        if let Some(a) = ap {
            self.bump(a);
        }
        self.bbox_add(x1, y1);
        self.bbox_add(x2, y2);
    }

    fn do_flash(&mut self, x: f64, y: f64) {
        match self.cur_ap {
            None => {
                self.push_rec(PRIM_ERROR, x, y, x, y, 0.0, 0.0, 0.0, 0.0);
                self.bbox_add(x, y);
            }
            Some(a) => {
                let w = self.apw(a).unwrap_or(0.0);
                let code = PRIM_FLASH + if self.negative { PRIM_CLEAR } else { 0.0 };
                self.push_rec(code, x, y, x, y, w, a as f64, 0.0, 0.0);
                if w != 0.0 && (self.minw.is_none() || w < self.minw.unwrap()) {
                    self.minw = Some(w);
                }
                self.bump(a);
                self.bbox_add(x, y);
            }
        }
    }

    fn run(&mut self) -> bool {
        let data = self.data;
        let mut i = 0usize;
        let n = data.len();
        while i < n {
            // 跳过命令之间的空白 (与 Python 侧的 strip/prev 判定等价)
            while i < n && is_ws(data[i]) {
                i += 1;
            }
            if i >= n {
                break;
            }
            let c = data[i];
            let prev_ok =
                i == 0 || matches!(data[i - 1], b'\n' | b'\r' | b'*' | b'%' | b' ' | b'\t');
            if c == b'%' && prev_ok {
                let j = match data[i + 1..].iter().position(|&b| b == b'%') {
                    Some(p) => i + 1 + p,
                    None => break,
                };
                if !self.ext_block(&data[i + 1..j]) {
                    return false;
                }
                i = j + 1;
            } else {
                let j = match data[i..].iter().position(|&b| b == b'*') {
                    Some(p) => i + p,
                    None => break,
                };
                if !self.cmd(&data[i..j]) {
                    return false;
                }
                i = j + 1;
            }
        }
        // 单位必须显式声明, 否则 Python 端要做推断 + 整体缩放
        if self.in_region {
            self.close_region();
        }
        if !self.fs_seen || self.unit_src.is_empty() {
            return false;
        }
        let max_abs = self
            .span
            .iter()
            .flatten()
            .fold(0.0f64, |m, v| m.max(v.abs()));
        if self.span[0].is_some() && max_abs >= 10f64.powi(self.int_d) {
            self.warnings.push("坐标超出 %FS 声明的整数位范围");
        }
        if self.zero_t {
            self.warnings.push("坐标为尾随零省略(FSTA)");
        }
        if self.scale != [1.0, 1.0] {
            self.warnings.push("含图像比例 %SF");
        }
        if self.mirror[0] || self.mirror[1] {
            self.warnings.push("含图像镜像 %MI");
        }
        if self.rot != 0 {
            self.warnings.push("含图像旋转 %IR");
        }
        true
    }
}

#[pyfunction]
fn parse(py: Python<'_>, data: &[u8]) -> PyResult<Option<PyObject>> {
    let d = if data.starts_with(&[0xEF, 0xBB, 0xBF]) {
        &data[3..]
    } else {
        data
    };
    let mut p = P::new(d);
    if !p.run() {
        return Ok(None);
    }
    let out = pyo3::types::PyDict::new_bound(py);
    let bytes = unsafe { std::slice::from_raw_parts(p.rec.as_ptr() as *const u8, p.rec.len() * 8) };
    out.set_item("rec", PyBytes::new_bound(py, bytes))?;
    out.set_item("n", p.n)?;
    out.set_item("feat", p.feat)?;
    out.set_item("minw", p.minw)?;
    out.set_item(
        "bbox",
        p.bbox.iter().map(|v| v.unwrap_or(0.0)).collect::<Vec<f64>>(),
    )?;
    out.set_item("has_bbox", p.bbox[0].is_some())?;
    out.set_item(
        "span",
        p.span.iter().map(|v| v.unwrap_or(0.0)).collect::<Vec<f64>>(),
    )?;
    out.set_item("has_span", p.span[0].is_some())?;
    out.set_item("unit", if p.inch { "inch" } else { "mm" })?;
    out.set_item("unit_src", p.unit_src)?;
    out.set_item("int_d", p.int_d)?;
    out.set_item("dec_d", p.dec_d)?;
    out.set_item("zero", if p.zero_t { "T" } else { "L" })?;
    out.set_item("coord", if p.incr { "I" } else { "A" })?;
    out.set_item("scale", p.scale.to_vec())?;
    out.set_item("off", p.off.to_vec())?;
    out.set_item("mirror", vec![p.mirror[0], p.mirror[1]])?;
    out.set_item("rot", p.rot)?;
    out.set_item("warnings", p.warnings.clone())?;
    let aps = PyList::empty_bound(py);
    for a in &p.aps {
        aps.append((a.code, a.raw.clone()))?;
    }
    out.set_item("aps", aps)?;
    let use_ = PyList::empty_bound(py);
    for a in &p.ap_use {
        use_.append((a.0, a.1))?;
    }
    out.set_item("ap_use", use_)?;
    let arcs = PyList::empty_bound(py);
    for a in &p.arcs {
        arcs.append(a.to_vec())?;
    }
    out.set_item("arcs", arcs)?;
    let regs = PyList::empty_bound(py);
    for (idx, pts) in &p.regions_out {
        regs.append((*idx, pts.clone()))?;
    }
    out.set_item("regions", regs)?;
    Ok(Some(out.into()))
}

#[pymodule]
fn gerber_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    Ok(())
}
