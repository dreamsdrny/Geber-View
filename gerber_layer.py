"""
Gerber 图层类型识别 (Gerber viewer 精简版)
==========================================
从原 gerber_design 模块中剥离出查看器唯一需要的 identify_layer():
优先采用 Gerber X2 的 %TF.FileFunction 属性, 其次按文件名约定
(Allegro / Protel / KiCad / Eagle 等命名习惯) 判定图层类型与所在面。

返回: {"kind", "side", "index", "name", "source"[, "plated"]}
  kind : copper | soldermask | silkscreen | paste | profile | drill | other
  side : top | bottom | inner | ""
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

KIND_COPPER = "copper"
KIND_MASK = "soldermask"
KIND_SILK = "silkscreen"
KIND_PASTE = "paste"
KIND_PROFILE = "profile"
KIND_DRILL = "drill"
KIND_OTHER = "other"

SIDE_TOP = "top"
SIDE_BOTTOM = "bottom"
SIDE_INNER = "inner"
SIDE_UNKNOWN = ""

SOURCE_X2 = "X2"
SOURCE_NAME = "name"
SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class LayerRule:
    """文件名识别规则。

    patterns 会统一编译为忽略大小写的正则；priority 越大越优先匹配。
    """

    patterns: list[str]
    kind: str
    side: str = SIDE_UNKNOWN
    index: Optional[int] = None
    priority: int = 0
    _compiled: list[re.Pattern] = field(init=False, repr=False)

    def __post_init__(self):
        compiled = [
            re.compile(p, re.IGNORECASE) for p in self.patterns
        ]
        object.__setattr__(self, "_compiled", compiled)

    def match(self, filename: str) -> bool:
        return any(p.search(filename) for p in self._compiled)


# ---------------------------------------------------------------------------
# 文件名规则表
# ---------------------------------------------------------------------------
# 顺序很重要：越具体、越容易误判的规则优先级越高。
# 顶层/底层铜层中的 "top"/"bot" 放在较后位置，避免把普通项目名误判成层。

NAME_RULES: list[LayerRule] = [
    # 钻孔：优先匹配，避免被 copper / mask 误判
    LayerRule(
        patterns=[
            r"npth",
            r"non[_\-]?plated",
            r"npt(?=[\._\-])",
            r"pth",
            r"plated",
            r"drill",
            r"\.drl$",
            r"\.drill$",
            r"\.nc$",
            r"\.ncd$",
            r"\.xln$",
            r"\.exc$",
            r"\.tap$",
            r"\.txt$",
            r"\.drl\.",
            r"钻孔",
            r"通孔",
            r"孔位",
        ],
        kind=KIND_DRILL,
        priority=100,
    ),

    # 板框 / 外形
    LayerRule(
        patterns=[
            r"profile",
            r"outline",
            r"board[_\-]?outline",
            r"edge[_\.]?cuts",
            r"route",
            r"rout",
            r"\.gko$",
            r"\.gm[1-9]\d*$",
            r"外形",
            r"轮廓",
            r"成型",
            r"板框",
            r"铣边",
        ],
        kind=KIND_PROFILE,
        priority=90,
    ),

    # 阻焊：顶层
    LayerRule(
        patterns=[
            r"solder[_\-]?mask",
            r"smask",
            r"mask[_\-]?top",
            r"top[_\-]?mask",
            r"\.gts$",
            r"\-f\.mask",
            r"(?:top|顶|正面|元件面).*(?:阻焊|防焊|绿油)",
            r"(?:阻焊|防焊|绿油).*(?:top|顶|正面|元件面)",
        ],
        kind=KIND_MASK,
        side=SIDE_TOP,
        priority=80,
    ),

    # 阻焊：底层
    LayerRule(
        patterns=[
            r"mask[_\-]?bot",
            r"mask[_\-]?bottom",
            r"bot[_\-]?mask",
            r"bottom[_\-]?mask",
            r"\.gbs$",
            r"\-b\.mask",
            r"(?:bot|bottom|底|背面|焊接面).*(?:阻焊|防焊|绿油)",
            r"(?:阻焊|防焊|绿油).*(?:bot|bottom|底|背面|焊接面)",
        ],
        kind=KIND_MASK,
        side=SIDE_BOTTOM,
        priority=80,
    ),

    # 阻焊：中文命名未标注面 -> 只判类型, 不猜面
    LayerRule(
        patterns=[r"阻焊", r"防焊", r"绿油", r"solder[_\-]?mask"],
        kind=KIND_MASK,
        priority=78,
    ),

    # 丝印：顶层
    LayerRule(
        patterns=[
            r"silkscreen[_\-]?top",
            r"top[_\-]?silk",
            r"silk[_\-]?top",
            r"legend",
            r"overlay",
            r"\.gto$",
            r"\-f\.silks",
            r"\.plc$",
            r"(?:top|顶|正面|元件面).*(?:丝印|文字|字符)",
            r"(?:丝印|文字|字符).*(?:top|顶|正面|元件面)",
        ],
        kind=KIND_SILK,
        side=SIDE_TOP,
        priority=70,
    ),

    # 丝印：底层
    LayerRule(
        patterns=[
            r"silkscreen[_\-]?bot",
            r"silkscreen[_\-]?bottom",
            r"bot[_\-]?silk",
            r"bottom[_\-]?silk",
            r"\.gbo$",
            r"\-b\.silks",
            r"\.pls$",
            r"(?:bot|bottom|底|背面).*(?:丝印|文字|字符)",
            r"(?:丝印|文字|字符).*(?:bot|bottom|底|背面)",
        ],
        kind=KIND_SILK,
        side=SIDE_BOTTOM,
        priority=70,
    ),

    # 丝印：中文命名未标注面 -> 只判类型
    LayerRule(
        patterns=[r"丝印", r"文字", r"字符", r"silkscreen", r"legend"],
        kind=KIND_SILK,
        priority=68,
    ),

    # 钢网：顶层
    LayerRule(
        patterns=[
            r"paste[_\-]?top",
            r"top[_\-]?paste",
            r"stencil[_\-]?top",
            r"solder[_\-]?paste",
            r"\.gtp$",
            r"\-f\.paste",
        ],
        kind=KIND_PASTE,
        side=SIDE_TOP,
        priority=60,
    ),

    # 钢网：底层
    LayerRule(
        patterns=[
            r"paste[_\-]?bot",
            r"paste[_\-]?bottom",
            r"bot[_\-]?paste",
            r"bottom[_\-]?paste",
            r"\.gbp$",
            r"\-b\.paste",
        ],
        kind=KIND_PASTE,
        side=SIDE_BOTTOM,
        priority=60,
    ),

    # 钢网/锡膏：中文命名
    LayerRule(
        patterns=[r"钢网", r"锡膏", r"paste", r"stencil"],
        kind=KIND_PASTE,
        priority=58,
    ),

    # 铜层：顶层
    LayerRule(
        patterns=[
            r"\.gtl$",
            r"\-f\.cu$",
            r"copper[_\-]?top",
            r"top[_\-]?copper",
            r"signal[_\-]?top",
            r"top[_\-]?signal",
            r"component",
            r"\.cmp$",
            r"art[_\-]?top",
            r"\.art$",
            r"(?:top|顶|正面|元件面).*(?:线路|铜箔|铜层)",
            r"(?:线路|铜箔|铜层).*(?:top|顶|正面|元件面)",
        ],
        kind=KIND_COPPER,
        side=SIDE_TOP,
        index=1,
        priority=50,
    ),

    # 铜层：底层
    LayerRule(
        patterns=[
            r"\.gbl$",
            r"\-b\.cu$",
            r"copper[_\-]?bot",
            r"copper[_\-]?bottom",
            r"bot[_\-]?copper",
            r"bottom[_\-]?copper",
            r"signal[_\-]?bot",
            r"signal[_\-]?bottom",
            r"solder",
            r"\.sol$",
            r"(?:bot|bottom|底|背面).*(?:线路|铜箔|铜层)",
            r"(?:线路|铜箔|铜层).*(?:bot|bottom|底|背面)",
        ],
        kind=KIND_COPPER,
        side=SIDE_BOTTOM,
        priority=50,
    ),

    # 铜层：内层 / 电源地平面
    LayerRule(
        patterns=[
            r"\.gp\d+$",
            r"inner",
            r"inr\d*",
            r"plane",
            r"\-in\d+\.cu",
            r"signal[_\-]?inner",
            r"inner[_\-]?\d+",
            r"内层",
            r"中间层",
            r"内电层",
        ],
        kind=KIND_COPPER,
        side=SIDE_INNER,
        priority=40,
    ),

    # 铜层：中文命名未标注面/层号 -> 只判类型
    LayerRule(
        patterns=[r"线路", r"铜箔", r"铜层", r"copper", r"signal"],
        kind=KIND_COPPER,
        side=SIDE_UNKNOWN,
        priority=39,
    ),

    # 兜底：纯数字后缀 .g1 / .g2 等，优先级最低
    LayerRule(
        patterns=[
            r"\.g\d+$",
        ],
        kind=KIND_COPPER,
        side=SIDE_INNER,
        priority=10,
    ),
]

# 按优先级降序排列，避免每次调用时重复排序
_NAME_RULES_SORTED = sorted(
    NAME_RULES, key=lambda r: r.priority, reverse=True
)


# ---------------------------------------------------------------------------
# X2 FileFunction 解析
# ---------------------------------------------------------------------------

def _parse_x2_file_function(file_function: str, name: str) -> Optional[dict]:
    """尝试从 %TF.FileFunction 解析层信息。"""

    ff = file_function.strip()
    if not ff:
        return None

    parts = [p.strip() for p in ff.split(",") if p.strip()]
    if not parts:
        return None

    head = parts[0].lower()
    side = ""
    idx: Optional[int] = None

    for p in parts[1:]:
        pl = p.lower()

        if re.fullmatch(r"l\d+", pl):
            idx = int(pl[1:])
        elif pl in ("top", "outer"):
            side = SIDE_TOP
        elif pl in ("bot", "bottom"):
            side = SIDE_BOTTOM
        elif pl in ("inr", "inner"):
            side = SIDE_INNER

    if head == "copper":
        return {
            "kind": KIND_COPPER,
            "side": side or SIDE_INNER,
            "index": idx,
            "name": name,
            "source": SOURCE_X2,
        }

    if head in ("soldermask", "solder_mask"):
        return {
            "kind": KIND_MASK,
            "side": side,
            "index": None,
            "name": name,
            "source": SOURCE_X2,
        }

    if head in ("legend", "silkscreen", "silk"):
        return {
            "kind": KIND_SILK,
            "side": side,
            "index": None,
            "name": name,
            "source": SOURCE_X2,
        }

    if head in ("solderpaste", "paste"):
        return {
            "kind": KIND_PASTE,
            "side": side,
            "index": None,
            "name": name,
            "source": SOURCE_X2,
        }

    if head in ("profile", "rout", "outline", "board"):
        return {
            "kind": KIND_PROFILE,
            "side": SIDE_UNKNOWN,
            "index": None,
            "name": name,
            "source": SOURCE_X2,
        }

    if head in ("drill", "plated", "nonplated"):
        plated = "npth" not in ff.lower() and "nonplated" not in ff.lower()
        return {
            "kind": KIND_DRILL,
            "side": SIDE_UNKNOWN,
            "index": None,
            "name": name,
            "source": SOURCE_X2,
            "plated": plated,
        }

    return None


# ---------------------------------------------------------------------------
# 文件名规则匹配
# ---------------------------------------------------------------------------

def _match_by_filename(filename: str) -> dict:
    """按文件名规则匹配层类型。"""

    low = filename.lower()

    for rule in _NAME_RULES_SORTED:
        if rule.match(low):
            return {
                "kind": rule.kind,
                "side": rule.side,
                "index": rule.index,
                "name": filename,
                "source": SOURCE_NAME,
            }

    return _unknown_result(filename)


def _unknown_result(name: str) -> dict:
    return {
        "kind": KIND_OTHER,
        "side": SIDE_UNKNOWN,
        "index": None,
        "name": name,
        "source": SOURCE_UNKNOWN,
    }


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def identify_layer(data: dict) -> dict:
    """识别一个 Gerber 层的类型。

    优先使用 X2 的 %TF.FileFunction，其次按文件名约定判断。
    """

    file_path = str(data.get("file") or "")
    name = str(data.get("name") or os.path.basename(file_path))
    file_function = str(data.get("file_function") or "").strip()

    if file_function:
        result = _parse_x2_file_function(file_function, name)
        if result is not None:
            return result

    return _match_by_filename(name)