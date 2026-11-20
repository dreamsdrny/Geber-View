# Qt / Rust 跨平台迁移原型（实验版）

**此原型不能替代现有 Python 查看器，也不能用于制造数据确认。** 旧版完整保留。
当前仅处理已声明 `%FSLAX...Y...` 和 `%MOMM/%MOIN` 的 Gerber：无孔圆形光圈、
正极性直线和圆形焊盘。只做同步、直接绘制，不代表最终大图性能。
光圈宏、非圆光圈、光圈孔、圆弧、区域、拼版、变换、负极性、X2 属性、钻孔和 A/B 对比尚未迁移；
不支持的文件将被拒绝而不是生成不完整图像。文件上限 128 MiB、图元上限 200 万。

## 目标和依赖

- 首个目标：Windows 10/11 64 位；后续移植 Linux/macOS。
- 开发机器：CMake 3.21+、Ninja、Qt 6.8.3 Widgets MinGW-w64 x64 套件和匹配的 MinGW 13.1 编译器、
  Rust GNU target `x86_64-pc-windows-gnu`。无需 MSVC。
  Qt 版本应选择仍支持 Windows 10 1809+ 与 Windows 11 的受支持版本；版本及 LGPL 条款需在安装前确认。
- 最终运行：部署文件夹内的程序、Rust DLL、Qt DLL/插件，以及目标系统需要的
  MinGW runtime DLL；无需 Python、.NET 或联网验证。是否可在目标电脑运行须实测。

## Windows 构建示例

以下命令在 Qt MinGW Kit 命令行或正确配置 MinGW `bin`、CMake、Ninja、Cargo 路径的
PowerShell 中执行；将 Qt 路径换为实际安装位置：

```powershell
cmake -S qt-viewer -B qt-viewer/build -G Ninja -DCMAKE_PREFIX_PATH="C:/Qt/6.8.3/mingw_64" -DRUSTUP_EXECUTABLE="C:/Users/<user>/.cargo/bin/rustup.exe" -DRUST_TOOLCHAIN=stable
cmake --build qt-viewer/build --config Release
cmake --build qt-viewer/build --target deploy
pwsh -File qt-viewer/test-viewer.ps1
```

`deploy` 目标使用 `windeployqt` 收集 Qt、MinGW runtime 和平台插件，并复制 `viewer_core.dll`；
部署目录在 `qt-viewer/build/package`。仍须检查每个 DLL 的许可证，并在干净目标电脑实测。
首次编译 Qt/Rust 及其依赖可能需要联网；如果开发机也不能联网，应预先准备离线工具链和依赖。

## 安全边界与测试

- Rust 库 `rust/viewer_core` 通过 `viewer_core.h` 暴露 C 接口；Qt 不调用 Python。
- Rust 核心对不支持的输入返回错误，C++ 显示文件名和原因。
- 在 Rust 工具链完整时运行：

```powershell
cargo test --manifest-path rust/gerber_core/Cargo.toml --no-default-features --locked
cargo test --manifest-path rust/viewer_core/Cargo.toml --locked
```

真实文件放入仓库根目录 `test-Gerber/` 后，可运行 `test-viewer.ps1` 查看 PASS/REJECT 结果。
仍需要在真实文件上与旧版、已知正确几何及其他可靠查看器交叉验证。原型没有经过
Qt 编译、打包、跨平台、所有格式和大图压力验收前，不能作为正式版本发布。
