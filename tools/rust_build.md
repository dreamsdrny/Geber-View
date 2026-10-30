# Rust 扩展构建配方（无管理员权限可用）

本机没有 MSVC / Visual Studio / gcc / clang / zig，也没有管理员权限。
下面这套配方已在实机验证可用：**rustup + windows-gnu 工具链 + 自带 rust-lld**，不需要任何 C 编译器、不需要 MSVC Build Tools。

## 已装好的东西

| 组件 | 位置 |
|---|---|
| rustup / cargo / rustc 1.99.1 | `%USERPROFILE%\.cargo\bin` |
| `stable-x86_64-pc-windows-gnu` 工具链 | `%USERPROFILE%\.rustup\toolchains\stable-x86_64-pc-windows-gnu` |
| 链接器 `rust-lld.exe` | `<toolchain>\lib\rustlib\x86_64-pc-windows-gnu\bin\rust-lld.exe` |
| 自带的 mingw 运行库 (.a) | `<toolchain>\lib\rustlib\x86_64-pc-windows-gnu\lib\self-contained` |

> 注意：`<toolchain>\bin\` 下只有 `rust-lldb`（调试器脚本），真正的链接器在 `lib\rustlib\...\bin\rust-lld.exe`，别搞混。

## 编译命令（验证过）

```powershell
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
$gnu = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-gnu\lib\rustlib\x86_64-pc-windows-gnu"
rustc +stable-x86_64-pc-windows-gnu `
  -C linker="$gnu\bin\rust-lld.exe" `
  -C link-self-contained=yes `
  hello.rs -o hello.exe
```

`cargo` 同理，在 crate 里放 `.cargo/config.toml`：

```toml
[target.x86_64-pc-windows-gnu]
linker = "C:\\Users\\<你>\\.rustup\\toolchains\\stable-x86_64-pc-windows-gnu\\lib\\rustlib\\x86_64-pc-windows-gnu\\bin\\rust-lld.exe"
rustflags = ["-C", "link-self-contained=yes"]
```

并用 `cargo +stable-x86_64-pc-windows-gnu build --release`（不要用默认的 msvc 工具链，那样找不到 `link.exe`）。

## 为什么不用 PyO3

PyO3 在 Windows 上链接 Python 需要一个 import lib（`python3.lib` / `libpython3.12.dll.a`）。
本机 Python 只带 MSVC 版 `.lib`，MinGW 版本没有，而生成它需要 `dlltool`（没装，且 winget 的 WinLibs 包在这台机器上解压失败）。

因此采用**纯 C ABI 的 `cdylib`**：Rust 侧不引用任何 Python 符号，Python 侧用 `ctypes` 加载 `.dll`。
既绕开了 import lib，也绕开了 PyO3 的版本绑定。

## 下一步（Rust 移植）的形状

- crate 类型：`cdylib`，产物 `gerber_core.dll`
- 入口：`gv_parse(data: *const u8, len: u64) -> *mut GvResult`，`gv_free(ptr)`
- 返回：定长记录数组（9 × f64 / 图元，与 Python `PrimStore` 完全一致）+ 头部标量 + 光圈定义原文
- **失败即回退**：Rust 只处理实现完整的子集，遇到不支持的结构返回 `UNSUPPORTED`，
  Python 端整体走原有解析器 —— 正确性由"逐字节相同或直接放弃"这条不变式保证
- 验收：`tools/eq_check.py` 在启用 native 后仍须 15/15 通过
- 打包：PyInstaller spec 里把 dll 加进 `binaries`
