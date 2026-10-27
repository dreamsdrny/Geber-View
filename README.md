# Evan_Gerber_View

<p align="center">
  <img src="Evan_Gerber_View.png" width="128" alt="Evan_Gerber_View logo">
</p>

<p align="center">
  <b>独立、轻量的 Gerber / NC Drill 查看器</b><br>
  多图层按类型自动着色 · A/B 多层叠加对比 · 自动定位差异
</p>

---

## 功能特性

- **多格式导入**：Gerber RS-274X / Gerber X2、Excellon NC Drill（`.gbr/.ger/.art/.gto/.gbo/.gts/.gbs/.gtl/.gbl/.gko/.gm1/.drl` 等），支持整目录批量导入。
- **图层自动着色**：按层类型（铜/阻焊/丝印/锡膏/外形/钻孔、顶/底/内层）自动分配颜色，属性面板可查看单位、格式、图幅、图元/孔数。
- **图层管理**：开/关、全开/全关、反选、上移/下移、删除；**双击图层名即可修改该层颜色**（KiCad 风格取色器）。
- **视图操作**：滚轮缩放、右键拖动平移、适应窗口、居中/镜像/偏移；选中层可单独拖动对齐。
- **比例校正**：自动识别不同 CAM 导出的单位/格式差异（英寸↔毫米、小数位错位、10/25.4 倍等），支持手动/自动校正与钻孔层对齐线路层。
- **A/B 叠加对比**：A、B 两组各可载入多层，自动按中心对齐；内置栅格化 XOR 差异分析，输出差异位置（XY）/面积并高亮，双击结果可放大定位。
- **流畅渲染**：解析与位图渲染均在后台线程；每层独立位图缓存 + 视口裁剪合成，缩放/平移复用缓存（静止时 1:1 免重采样），大文件不卡顿。
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

# 方式二：手动
python -m PyInstaller --clean --noconfirm Evan_Gerber_View.spec
```

产物：`dist/Evan_Gerber_View/Evan_Gerber_View.exe`

> 若图标缺失，可先运行 `python make_icon.py` 重新生成 `Evan_Gerber_View.ico / .png`。

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
Evan_Gerber_View.py     程序入口
gerber_viewer.py        查看器界面与渲染 (缩放/平移/A-B对比/差异定位)
gerber_reader.py        Gerber / Excellon NC Drill 解析
gerber_layer.py         图层类型识别
win_dpi.py              Windows 高 DPI 感知与窗口适配
branding.py             品牌 / 标题 / 图标路径
make_icon.py            图标生成 (Qiao 紫色 Logo)
Evan_Gerber_View.spec   PyInstaller 打包配置
build.bat               一键打包脚本
```

## 说明

本项目从 BRD-AI 工程中**仅剥离 Gerber 查看器部分**，去除对其余模块的依赖，可独立运行与打包。

## License

MIT