# Evan_Gerber_View

<p align="center">
  <img src="Evan_Gerber_View.png" width="128" alt="Evan_Gerber_View logo">
</p>

<p align="center">
  <b>独立、轻量的 Gerber / NC Drill 查看器</b><br>
  多图层按类型自动着色 · A/B 多层叠加对比 · 自动定位差异
</p>

<p align="center">
  <a href="https://github.com/dreamsdrny/Geber-View/actions/workflows/build.yml"><img src="https://github.com/dreamsdrny/Geber-View/actions/workflows/build.yml/badge.svg" alt="build-and-test"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
</p>

---

## 功能特性

- **多格式导入**：Gerber RS-274X / Gerber X2、Excellon NC Drill（`.gbr/.ger/.art/.g1..g99/.gp1/.gm2/.gto/.gbo/.gts/.gbs/.gtl/.gbl/.gko/.drl/.nc/.ncd/.xln/.txt` 等），支持整目录批量导入；制程量测导出的 `.txt`/无后缀文件按内容自动识别，无需先选“全部文件”。选一个文件即可自动带入同目录全部 Gerber（可取消勾选“自动带入同目录”）；批量层数较多时默认隐藏内层铜/钢网/盲埋孔，避免画面过乱。
- **图层自动着色**：按层类型（铜/阻焊/丝印/锡膏/外形/钻孔、顶/底/内层）自动分配颜色，属性面板可查看单位、格式、图幅、图元/孔数。
- **图层管理**：开/关、全开/全关、反选、上移/下移、删除；**双击图层名即可修改该层颜色**（KiCad 风格取色器）。
- **视图操作**：滚轮缩放、右键拖动平移、适应窗口、居中/镜像/偏移；选中层可单独拖动对齐。
- **比例校正**：自动识别不同 CAM 导出的单位/格式差异（英寸↔毫米、小数位错位、10/25.4 倍等），支持手动/自动校正与钻孔层对齐线路层。
- **A/B 叠加对比**：A、B 两组各可载入多层，自动按中心对齐；内置栅格化 XOR 差异分析，输出差异位置（XY）/面积并高亮，双击结果可放大定位。
- **流畅渲染**：解析与位图渲染均在后台线程；每层独立位图缓存 + 视口裁剪合成，缩放/平移复用缓存（静止时 1:1 免重采样）；大图层放大时只重渲视口高清块、不再重光栅化整层。**平移时按整数像素直接搬运上一帧，不再因取整误差触发 1px 重采样，画面稳定不抖；拖动单层时只重新合成这一层，其余层缓存为背景/前景（6 层实测约 2.4× 帧率）**；视口变化后过期的后台光栅化会提前中止，不再白占 CPU。Rust 原生解析已覆盖 X2 网络属性，30 万图元板约 65ms 解析（纯 Python 约 1s），同一文件重复打开走结果缓存。
- **导出 PNG**：一键导出当前可见图层为 PNG。

## 运行（源码）

```bash
pip install -r requirements.txt
python Evan_Gerber_View.py
```

## 打包为 Windows 可执行文件

```bash
# 方式一：一键脚本
build.bat

# 方式二：手动（目录版，带 _internal）
python -m PyInstaller --clean --noconfirm Evan_Gerber_View.spec

# 方式三：单文件版（全部依赖打进 exe，不再需要 _internal）
python -m PyInstaller --clean --noconfirm Evan_Gerber_View_onefile.spec
```

产物：
- 目录版：`dist/Evan_Gerber_View/Evan_Gerber_View.exe`（**推荐分发**）
- 单文件版：`dist/Evan_Gerber_View.exe`（约 17MB，首次启动会解压到临时目录，略慢 1~2 秒）

> 若图标缺失，可先运行 `python make_icon.py` 重新生成 `Evan_Gerber_View.ico / .png`。

### 该用哪个产物？

| 产物 | 优点 | 局限 |
| --- | --- | --- |
| **目录版** `dist/Evan_Gerber_View/`（整个文件夹） | 启动即可用，**不做运行时解压**，最快、最不容易被安全软件拦；整目录拷贝即可分发 | 必须整个文件夹一起拷，不能只拷 exe |
| 单文件版 `dist/Evan_Gerber_View.exe` | 只有一个文件，方便传阅 | 每次启动都要先把内容解压到 `%TEMP%`；若临时目录不可写/被安全软件拦截，会弹 `Error` 框打不开 |

### 双击 exe 弹出标题为 `Error` 的框 / 打不开？

单文件版启动时由 PyInstaller 引导程序把依赖解压到临时目录，解压失败时就会弹这种框（典型报错
`Failed to extract ... fopen: Permission denied`）。这不是 Gerber 数据的问题，按下面任一方式即可打开：

1. **直接用目录版**：`dist\Evan_Gerber_View\Evan_Gerber_View.exe`（不依赖运行时解压，最稳）。
2. **把临时目录指到可写位置再启动单文件版**：新建 `启动.bat`，内容如下，双击它即可：
   ```bat
   @echo off
   set "TEMP=%LOCALAPPDATA%\Evan_Gerber_View\tmp"
   set "TMP=%TEMP%"
   if not exist "%TEMP%" mkdir "%TEMP%"
   start "" "%~dp0Evan_Gerber_View.exe" %*
   ```
3. **检查安全软件**：把程序目录/该 exe 加入白名单（不少企业安全策略禁止"从临时目录解压并执行"）。

若程序启动时抛异常，会在 exe 同级目录写 `Evan_Gerber_View_crash.log`（不可写时写到
`%LOCALAPPDATA%\Evan_Gerber_View\crash.log`），排查"打不开"时先看这个日志。

### 持续集成（GitHub Actions）

推送到 `main` 会触发 [build-and-test](.github/workflows/build.yml)：语法检查 → 回归测试
（解析器 + 真实 Tk 渲染）→ 打包目录版与单文件版 → **冒烟测试（两个 exe 必须真的能打开）** →
上传构建产物（Actions 页面里的 `Evan_Gerber_View-windows-x64`）。

## 使用说明

| 操作 | 说明 |
| --- | --- |
| 导入Gerber… / 导入钻孔… / 导入全部(目录)… | 载入制造数据 |
| 对比A / 对比B: 导入多层 | 载入两组做叠加对比 |
| 自动定位差异 | 计算 A/B 差异并高亮，双击结果定位 |
| 改颜色 / 双击图层名 | 修改选中图层颜色 |
| 拖动移动图层 | 勾选后可左键拖动选中层；右键拖动=平移，滚轮=缩放 |
| 比例校正 | 修正不同 CAM 的单位/格式差异 |

## 目录结构

```
Evan_Gerber_View.py           程序入口
gerber_viewer.py              查看器界面与渲染 (缩放/平移/A-B对比/差异定位)
gerber_reader.py              Gerber / Excellon NC Drill 解析
gerber_layer.py               图层类型识别
gerber_native.py              Rust 原生解析胶水 (缺失自动回退纯 Python)
win_dpi.py                    Windows 高 DPI 感知与窗口适配
branding.py                   品牌 / 标题 / 图标路径
make_icon.py                  图标生成 (Qiao 紫色 Logo)
test_gui.py                   端到端回归 (解析 + 真实 Tk 渲染)
tools/smoke_test_exe.ps1      冒烟测试: 验证打包出的 exe 真的能打开
Evan_Gerber_View.spec         目录版打包配置
Evan_Gerber_View_onefile.spec 单文件版打包配置
build.bat                     一键打包脚本
.github/workflows/build.yml   GitHub Actions: 回归 + 打包 + exe 冒烟测试
```

## 说明

本项目从 BRD-AI 工程中**仅剥离 Gerber 查看器部分**，去除对其余模块的依赖，可独立运行与打包。

## License

MIT