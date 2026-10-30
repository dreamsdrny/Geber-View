# gerber_core — Rust 原生解析内核

纯 Python 解析器在 9.4MB / 30 万图元的板上要 ~1.0s；换成 Rust 后 **~60ms**。
本目录是把 `gerber_reader.parse_gerber` 的**热路径**用 PyO3 重写的原生扩展。

## 设计铁律：要么逐字节等价，要么完全不用

Rust 只接管它能**确定性复刻** Python 语义的子集。遇到下面任何一种，`parse()` 直接返回 `None`，
Python 端整体回退到原解析器 —— 所以不存在"快了但画错"的中间态：

| 不支持（→ 回退纯 Python） |
| --- |
| `%AM` 光圈宏 / `%SR` 拼版 / `%AS` 轴选择 |
| `G74` 单象限圆弧（I/J 是绝对值，要按走向选圆心符号） |
| X2 属性 `%TF` `%TA` `%TO` `%TD`（网络名/refdes） |
| 未声明单位 / 未声明 `%FS` |
| 带小数点的坐标（`X1.5Y2`） |
| `%AD` 形状非 C/R/O/P |

当前语料 17 个文件里 **11 个走原生**（含 30 万图元的基准板、区域、G75 圆弧），
6 个自动回退且结果仍逐字节一致。

圆弧的**包围盒采样故意留在 Python**：`_arc_sample` 用 `hypot/atan2/cos`，
跨语言的最后 1 ULP 可能不同，而 bbox 要按位比对。Rust 只返回
`arcs = [(x1,y1,x2,y2,i,j,cw)]`，Python 端再补采样点 —— 既拿到原生速度，又保证位级一致。

## 目录

```
gerber_core/
  Cargo.toml            pyo3 0.22, cdylib, lto=true codegen-units=1 panic="abort"
  .cargo/config.toml    用 gnu 工具链自带的 rust-lld, 免装 MSVC / MinGW
  src/lib.rs            ~700 行解析器 + PyO3 导出
  gerber_core.pyd       构建产物 (由 dll 复制而来, 打包用)
```

## 构建

```powershell
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
cd rust\gerber_core
cargo +stable-x86_64-pc-windows-gnu build --release
Copy-Item target\release\gerber_core.dll gerber_core.pyd -Force
```

必须显式指定 `+stable-x86_64-pc-windows-gnu` 工具链（host == target，不要加 `--target`，
否则产物会跑到 `target/x86_64-pc-windows-gnu/release/`）。链接器用 `rust-lld` +
`-C link-self-contained=yes`，因此**不需要 MSVC 构建工具，也不需要管理员权限**。

## 验证

```powershell
# 逐字节对拍 16 个语料 (Python 旧版 vs 当前版, 含原生路径)
.\.pyfull\python.exe -B tools\eq_check.py

# 强制关闭原生, 确认纯 Python 路径同样 16/16
$env:GERBER_NO_NATIVE="1"; .\.pyfull\python.exe -B tools\eq_check.py

# 看每个文件实际走的是哪条路 (回退原因会打出来)
.\.pyfull\python.exe -B -c "import glob,os,gerber_native as N; [print(os.path.basename(f), 'NATIVE' if N.parse(f) else 'fallback:'+str(N._LAST)) for f in sorted(glob.glob('tools/_corpus/*.gbr'))]"
```

## 打包

`Evan_Gerber_View.spec` 已把 `rust/gerber_core/gerber_core.pyd` 作为 binary 打进
`_internal/`；`gerber_native.py` 在 `sys.executable` 目录与 `sys._MEIPASS` 里找它。
**扩展模块必须是 `.pyd` 后缀**，`.dll` 名字 importlib 不认。

exe 自带无界面自检（打包后可直接量真实耗时）：

```powershell
dist\Evan_Gerber_View\Evan_Gerber_View.exe --selftest a.gbr b.gbr
# 结果写到 exe 同目录 selftest.txt
```

## 性能（同一台机器）

| 文件 | 图元 | 原 Python | 纯 Python 优化 | Rust |
|---|---|---|---|---|
| `_bench300k.gbr` 9.4MB | 300,000 | 2.92 s | 1.03 s | **0.065 s** |
| `15_big.gbr` 0.54MB | 15,500 | 0.17 s | 0.057 s | **0.005 s** |

即 300k 图元：**45× vs 原版**，吞吐约 150 MB/s。

## 下一步能提覆盖率（按性价比排序）

1. `%AM` 常见宏（C/R/O/P/Outline）→ 覆盖大量历史 Gerber
2. `G74` 单象限圆弧（`_arc_center_sq` 需要 atan2，按上面圆弧的办法把圆心选择留在 Python 即可）
3. `%SR` 拼版复制（纯平移，无浮点风险）
4. X2 `%TO`/`%TD` 网络属性 → 省掉纯 Python 的 extras 组装
