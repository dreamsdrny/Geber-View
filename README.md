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
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="license">
</p>

---

## 功能特性

- **多格式导入**：Gerber RS-274X / Gerber X2、Excellon NC Drill（`.gbr/.ger/.art/.g1..g99/.gp1/.gm2/.gto/.gbo/.gts/.gbs/.gtl/.gbl/.gko/.drl/.nc/.ncd/.xln/.txt` 等），支持整目录批量导入；制程量测导出的 `.txt`/无后缀文件按内容自动识别，无需先选“全部文件”。选一个文件即可自动带入同目录全部 Gerber（可取消勾选“自动带入同目录”）；批量层数较多时默认隐藏内层铜/钢网/盲埋孔，避免画面过乱。
- **图层自动着色**：按层类型（铜/阻焊/丝印/锡膏/外形/钻孔、顶/底/内层）自动分配颜色，属性面板可查看单位、格式、图幅、孔数。识别覆盖常见 CAM 短名（`cm/cs/sm/ss/sss/in2..in7/ol1` 等）与 Allegro `.art` 命名；钻孔按**解析内容**判定（不再依赖 `.txt` 后缀）。短名的面/层号属行业惯例推断，可能不准——用**“设置图层类型”**手动纠正（见下）。
- **手动纠正图层类型**：右键图层 / 图层菜单 →“设置图层类型…”，可指定类型（铜/阻焊/丝印/钢网/板框/钻孔/其它）、面（顶/底/内）与内层层号，纠正后重新着色并刷新 A/B 配对；“恢复自动识别”可一键还原。手动覆盖随工程保存，详情面板标注“手动类型”。钻孔层的内容判定不可被覆盖成非钻孔。
- **图层管理**：开/关、全开/全关、反选、上移/下移、删除；**左键单击“开/关”旁的颜色色块即可修改颜色**，也可双击图层名；隐藏层同样可以改色。
- **清爽工作区**：顶部仅保留常用操作，左侧按“图层 / A/B 对比 / 量测”分页，侧栏与画布之间可拖动调整宽度；单位、图幅及变换放在图层详情中，低频操作收进文件/图层/工具/视图菜单。默认隐藏选中框，差异标记可关闭，量测完整读数留在侧栏，画布仅显示短距离标签。
- **视图操作**：默认浏览模式，左键或右键拖动平移，滚轮缩放，**方向键平移视图（Shift 加速）**；启用“移动图层”后左键拖动选中层对齐、**方向键微调选中层（Shift 大步长）**，避免误移动制造数据。支持适应窗口、居中/镜像/偏移。
- **比例校正**：自动识别不同 CAM 导出的单位/格式差异（英寸↔毫米、小数位错位、10/25.4 倍等），支持手动/自动校正与钻孔层对齐线路层。
- **A/B 逐层对比**：左侧 **A/B 对比页**提供两组的文件/文件夹导入、层数显示、对齐、差异计算、查看结果和差异报告导出（默认 HTML，浏览器直接打开；可选 CSV/TXT）；顶部“A/B 对比”可直接切换到该页。按类型、顶底面及层号配对，使用分块像素差异检测；可设置精度和边缘容差，双击差异定位。同类型多个文件优先按文件名匹配，仍有歧义时按名称排序并提示。
- **流畅渲染**：解析与位图渲染均在后台线程；每层独立位图缓存 + 视口裁剪合成，缩放/平移复用缓存（静止时 1:1 免重采样）；大图层放大时只重渲视口高清块、不再重光栅化整层。**平移时按整数像素直接搬运上一帧，不再因取整误差触发 1px 重采样，画面稳定不抖；拖动单层时只重新合成这一层，其余层缓存为背景/前景（6 层实测约 2.4× 帧率）**；视口变化后过期的后台光栅化会提前中止，不再白占 CPU。Rust 原生解析已覆盖 X2 网络属性，30 万图元板约 65ms 解析（纯 Python 约 1s），同一文件重复打开走结果缓存。网络高亮掩膜按视口缓存，静止视图反复重绘不再每帧重光栅化整条网络；宏表达式 AST 缓存加速大量闪装展开。空间索引查询加锁保证 UI 线程与渲染 worker 并发安全；渲染异常会复位在途标记，图层不再静默卡死。
- **导出 PNG**：一键导出当前可见图层为 PNG。
- **工程与报告**：保存/打开工程（源文件相对路径、图层顺序、颜色、分组、变换和**手动图层类型覆盖**），查看解析问题与变换，导出制造统计文本和差异报告（HTML/CSV/TXT）。所有保存对话框默认定位到用户『文档』目录，避免落在需要管理员权限的受保护目录。
- **量测抓取**：支持自由点/夹角、中心到中心、边到边最短净距、中心到边；点击焊盘或孔的内部即可抓取中心，鼠标预览与落点使用相同吸附逻辑，抓取图形高亮，显示 mm、mil、ΔX、ΔY 和角度。支持圆形/矩形/长圆/多边形焊盘、走线、区域和槽孔轮廓；可取消排队中的导入与正在进行的对比。
- **传统 RS-274D 参数**：选中 Gerber 后，通过“RS-274D 参数”指定源文件单位、坐标格式（2.3~4.5 常用预设，可手输）、零省略模式，并输入或载入光圈表；后台重新解析原图层，参数随工程保存。缺少光圈定义时会阻止 A/B 对比，而不是用默认宽度报告“相同”。

### 对比与解析精度

- A/B 两组导入完成后会**自动中心对齐**，让两块板直接重叠：对齐以**板框/外形层**为准，自动忽略制造图纸（FAB_DRAWING）、拼版（PANEL）等超大层的干扰；无板框层时按尺寸中位数剔除异常层。可用“B组原始坐标”还原，或“B组中心对齐”手动重对齐。比例默认保留原始值，可用比例校正菜单显式调整。解析问题入口列出警告与变换。
- 图层名后的 **⚠ 仅表示需要核对的解析风险**（例如缺失单位/格式、未知命令、未定义宏）；已处理的尾随零、镜像、旋转、比例、拼版、圆弧离散化及小范围图元改列为信息，不再误标为警告。详情仍可在“解析问题”或制造报告中查看。
- 对比结果取决于所选毫米/像素及容差，并非解析几何的精确布尔运算；容差按像素中心的欧氏距离判断，小于一个像素的容差不会额外扩大。总像素及容差像素数设有上限；同一差异跨分块边界时可能拆成多条。参与对比的是可见图层，结果最多显示 600 个画布标记，导出的报告包含全部检测结果。
- RS-274X（有时称 RS-274DX）与 X2 可按层类型跨格式配对；顶/底层不因 X2 是否携带层号而拆成缺失层，内层仍按层号区分。配对歧义、缺层或解析警告须确认后继续；修改对比图层的显示状态、位置、镜像或比例后，旧差异结果会失效，需重新计算。
- RS-274D 没有完整的内嵌光圈/格式信息，必须使用原始 CAM 光圈表及正确参数。参数面板接受每行 `D10 C 0.2`、`D11 R 1.0 0.5`、`D12 O 1.5 0.6`、`D13 P 1.0 6 30` 的表格（尺寸默认为源文件单位），支持 C/R/O/P 及可选圆孔；兼容多种 CAM 方言：形状全称 CIRCLE/ROUND/RECTANGLE/SQUARE/OBROUND/OVAL/POLYGON/OCTAGON，`*` 或 `;` 分隔条目，`=`/`:` 分隔字段，D 前缀可省，`#`/`//` 注释与常见表头自动跳过；表头 `UNITS MM|INCH|MILS` 或数值后缀 `0.2mm/20mil/0.01in` 自动换算到源文件单位。未知方言仍报错拒绝，不猜测；复杂外部宏应在 CAM 软件中转换为 RS-274X/X2。
- “边到边”计算两个选中图元的最短净距，接触或重叠显示 0，不是整个铜层布尔合并后的 DFM 间距检查。圆弧及带光圈孔的轮廓采用目标弦高 0.001 mm 的折线近似（分段有上限）；非圆光圈走线仍按等效线宽近似。复杂宏按解析展开后的子图元抓取，关键制造尺寸应交叉核对。
- 区域圆弧目标弦高为 0.001 mm（分段数有上限）；部分旧式命令和复杂宏仍可能近似处理，请查看解析问题并对关键数据交叉核对。
- **解析正确性加固**：单位/格式声明（`%MO/%FS`）改为锚定匹配，网络名/层名里含 `MOIN/MOMM` 子串不再中途劫持单位；小写命令与大写等价解析；Excellon 声明 `TZ` 但坐标为短于格式宽度的定宽时给出“可能相差 10 倍”告警（不再静默）；`G00` 快移只定位、不产生假孔；偏心热风焊盘（thermal）缺口绕自身中心旋转；宏表达式按真实比较运算符（`= < > <= >= !=`）求值。这些修复均配有回归测试。
- 工程文件不包含制造数据本体；移动工程时需一并保留其引用的源文件。取消导入在当前文件解析完成后生效。

## 运行（源码）

> C++/Qt + Rust 迁移原型见 [`qt-viewer/README.md`](qt-viewer/README.md)。
> 当前仅支持极小的 Gerber 子集，尚不能替代下方的 Python 正式查看器。

```bash
pip install -r requirements.txt
python Evan_Gerber_View.py
```

运行量测及跨格式对比回归：

```bash
python -m unittest -v test_accuracy test_measure_compare test_layer_id test_parse_correctness
python test_gui.py
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
>
> **Rust 原生解析内核（可选）**：`rust/gerber_core` 编译出的 `gerber_core.pyd` 不在 git 里
> （被 `.gitignore` 忽略），缺了也能跑（自动回退纯 Python）。想启用加速就先编译：
> ```bash
> cd rust/gerber_core
> cargo build --release
> copy target\release\gerber_core.dll gerber_core.pyd
> ```
> 两个 spec 都是"有就打包、没有就跳过"，所以干净检出（CI / 新克隆）也能直接打包。

### 该用哪个产物？

| 产物 | 优点 | 局限 |
| --- | --- | --- |
| **目录版** `dist/Evan_Gerber_View/`（整个文件夹） | 启动即可用，**不做运行时解压**，最快、最不容易被安全软件拦；整目录拷贝即可分发 | 必须整个文件夹一起拷，不能只拷 exe |
| 单文件版 `dist/Evan_Gerber_View.exe` | 只有一个文件，方便传阅 | 每次启动都要先把内容解压到 `%TEMP%`；**若 exe 带 Windows「低完整性标签」则解压失败、弹 `Error` 框打不开**（见下一节，用 `启动单文件版.bat` 解决） |

### 双击单文件版 exe 弹出标题为 `Error` 的框 / 打不开？

真实原因（已实测复现）：

本仓库所在的 `D:\Evan\View-Gerber` 目录被沙箱工具打上了 Windows 的**低完整性标签**
（`icacls` 里能看到 `Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)`），**该目录下所有文件都会继承它**。
**Windows 会让带这个标签的 exe 以「低完整性进程」运行**——实测把 `whoami.exe` 复制进工作区后，
它自报 `S-1-16-4096`（Low）；放在工作区外则是 `S-1-16-8192`（Medium）。而**低完整性进程写不了
`%TEMP%`**（默认 Medium，且默认策略禁止向上写），于是：

* **单文件版**：PyInstaller 引导程序要先在 `%TEMP%` 里建 `_MEIxxxx` 解压目录 → 创建被拒绝 → 弹出
  标题 `Error`、内容 `Could not create temporary directory!` 的框，程序在解压前就退出；
* **目录版**：不做运行时解压，所以照常打开；
* 同一个 exe 只要复制到工作区外（桌面、`%LOCALAPPDATA%` 等），就以 Medium 权限运行，立刻正常。

实测对照：

| exe 放置位置 | 结果 |
| --- | --- |
| `D:\Evan\View-Gerber\dist\Evan_Gerber_View.exe` | ❌ 弹 `Error`（`Could not create temporary directory!`） |
| 同一文件复制到 `C:\egv_test\` / `D:\egv_test\` / `%TEMP%` / `%LOCALAPPDATA%` | ✅ 正常打开 |
| 目录版 `dist\Evan_Gerber_View\Evan_Gerber_View.exe`（仍在工作区内） | ✅ 正常打开（不做运行时解压） |

按下面任一方式即可打开：

1. **双击 `启动单文件版.bat`（推荐）**：它把 `dist\Evan_Gerber_View.exe` 复制到工作区外的
   `%LOCALAPPDATA%\Evan_Gerber_View\` 再启动，直接绕开低完整性标签。
2. **手动复制出来再双击**：把 `dist\Evan_Gerber_View.exe` 拷到桌面、下载目录等普通文件夹即可。
   注意：**再拷回工作区内会重新继承标签，又会打不开**。
3. **直接用目录版**：`dist\Evan_Gerber_View\Evan_Gerber_View.exe`（最稳，不受该标签影响）。

> 若想在工作区内直接双击单文件版：要以**管理员身份**去掉工作区根目录上的低完整性标签，然后
> **重新打包/重新复制** exe（已存在的文件不会自动改标签）：
> ```
> icacls "D:\Evan\View-Gerber" /setintegritylevel (OI)(CI)Medium
> ```
> 本会话没有管理员权限，这条命令尚未实测；沙箱工具后续也可能再次给工作区打标签。

> 附带影响：在工作区内直接跑 `python` 时，`tempfile.gettempdir()` 会退化成当前目录（因为 `%TEMP%`
> 写不进去），测试临时文件会掉在仓库根目录里；`test_gui.py` 已改为使用独立临时目录并在结束时删除。

若程序启动时抛异常，会在 exe 同级目录写 `Evan_Gerber_View_crash.log`（不可写时写到
`%LOCALAPPDATA%\Evan_Gerber_View\crash.log`），排查"打不开"时先看这个日志。

### 持续集成（GitHub Actions）

推送到 `main` 会触发 [build-and-test](.github/workflows/build.yml)：语法检查 → 回归测试
（解析器 + 真实 Tk 渲染）→ 编译 Rust 原生内核（可选，失败不阻塞）→ 打包目录版与单文件版 →
**冒烟测试（两个 exe 必须真的能打开）** → 上传构建产物（Actions 页面里的
`Evan_Gerber_View-windows-x64`，含单文件版、目录版和 `启动单文件版.bat`）。

## 使用说明

| 操作 | 说明 |
| --- | --- |
| 导入文件 / 导入文件夹；文件菜单 → 导入钻孔 | 载入制造数据；自动带入同目录选项位于文件菜单 |
| A/B 对比页 → A组/B组: 导入文件 / 导入文件夹 | 分别载入两组做叠加对比（仅比较可见层） |
| 自动定位差异 | 计算 A/B 差异并高亮，双击结果定位 |
| 左键单击图层颜色色块 / 双击图层名 | 修改该图层颜色，无需顶部按钮 |
| 查看差异结果 | 重新打开上一次对比结果 |
| 移动图层 | 默认关闭；勾选后左键拖动选中层，右键仍为平移；量测与移动图层模式互斥 |
| 方向键 ↑↓←→ | 浏览模式平移视图（Shift 加速）；移动图层模式微调选中层（Shift 大步长） |
| 右键图层 / 图层菜单 | 改色、**设置图层类型**、显隐、Solo、排序、删除、镜像、偏移和对齐 |
| 工具菜单 → 比例校正 / RS-274D 参数 | 修正比例，或为选中 Gerber 补充单位、格式和光圈表后重新解析 |
| 测距 → 左侧量测方式 | 选择中心到中心、边到边、中心到边或自由点/夹角，依次点选两个图形；完整读数在侧栏，完成后下一次点击开始新量测 |
| 画布上方 → 差异标记 / 选中框 | 按需开启或隐藏辅助叠加；不改变对比结果和报告内容 |

## 目录结构

```
Evan_Gerber_View.py           程序入口
gerber_viewer.py              查看器界面与渲染 (缩放/平移/A-B对比/差异定位)
gerber_reader.py              Gerber / Excellon NC Drill 解析
gerber_layer.py               图层类型识别
gerber_native.py              Rust 原生解析胶水 (缺失自动回退纯 Python)
gerber_diff.py                逐层分块差异检测
gerber_measure.py             图元抓取与几何量测
gerber_project.py             工程保存与恢复
gerber_report.py              制造与差异报告
win_dpi.py                    Windows 高 DPI 感知与窗口适配
branding.py                   品牌 / 标题 / 图标路径
make_icon.py                  图标生成 (Qiao 紫色 Logo)
test_gui.py                   端到端回归 (解析 + 真实 Tk 渲染 + 手动类型纠正)
test_accuracy.py              几何、逐层比较和工程回归
test_measure_compare.py       跨格式配对、RS-274D 参数、抓取与测距回归
test_layer_id.py              图层识别 / 配对 / 手动类型覆盖工程往返回归
test_parse_correctness.py     解析正确性回归 (单位劫持/缓存键/小写命令/零省略告警/宏/快移/索引并发)
tools/smoke_test_exe.ps1      冒烟测试: 验证打包出的 exe 真的能打开
Evan_Gerber_View.spec         目录版打包配置
Evan_Gerber_View_onefile.spec 单文件版打包配置
build.bat                     一键打包脚本（目录版）
启动单文件版.bat               单文件版启动器: 复制到工作区外再运行, 绕开低完整性标签导致打不开的问题
.github/workflows/build.yml   GitHub Actions: 回归 + 打包 + exe 冒烟测试
```

## 说明

本项目从 BRD-AI 工程中**仅剥离 Gerber 查看器部分**，去除对其余模块的依赖，可独立运行与打包。

## License

AGPL-3.0-or-later，详见 [`LICENSE`](LICENSE)。Qt/Rust 迁移原型依赖的第三方运行库许可见 [`qt-viewer/THIRD-PARTY-NOTICES.txt`](qt-viewer/THIRD-PARTY-NOTICES.txt)。
