"""数据模型定义。

使用 ``dataclasses`` 定义展示与传递所需的数据结构，并基于 Bangumi API v0
（https://api.bgm.tv/v0/）返回的原始 JSON 提供容错的 ``from_xxx`` 解析工厂方法。

设计原则：
- 模型层保持纯净，不依赖网络层与 rich 展示层，便于单测；
- 所有解析均“尽力而为”：字段缺失或类型意外时取默认值，绝不抛异常；
- 数据类字段与字段名保持稳定（界面层依赖），只改变“原始字典 -> 数据类”的映射来源。

常见 Bangumi 字段说明（见 https://bangumi.github.io/api/）：
- ``id``      ：条目 ID（映射为 season_id，界面交互沿用）
- ``name`` / ``name_cn``：原名 / 中文名（映射为番剧标题）
- ``summary`` ：简介
- ``date`` / ``air_date``：播出 / 上映日期
- ``rating``  ：评分对象，``rating.score`` 为评分，``rating.total`` 为评分人数
- ``eps``     ：集数（数字表示总集数，个别接口为数组）
- ``images``  ：封面图对象（large/common/medium/…）
- ``collection_total`` / ``collects`` / ``rank``：收藏数 / 排名
- ``infobox`` ：详细信息条目（地区、制作人员等）
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 基础工具函数
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: Optional[Any]) -> str:
    """清理接口返回的文本（去掉 ``<em>`` 等标签并反转义 HTML 实体）。

    Args:
        value: 原始文本（可能为 None 或非字符串类型）。

    Returns:
        清理后的纯文本。
    """
    if value is None:
        return ""
    text = str(value).strip()
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text).strip()


# Bangumi 条目类型：1 书籍 / 2 动画 / 3 音乐 / 4 游戏 / 6 三次元
_BANGUMI_TYPE_NAMES: Dict[int, str] = {
    0: "未知",
    1: "书籍",
    2: "动画",
    3: "音乐",
    4: "游戏",
    6: "三次元",
}

# 类别别名字典：把接口里的英文代号翻译成中文名（兼容历史调用）
_CATEGORY_ALIASES: Dict[str, str] = {
    "bangumi": "番剧",
    "media_bangumi": "番剧",
    "guochuang": "国创",
    "media_guochuang": "国创",
    "ft": "影视",
    "media_ft": "影视",
    "documentary": "纪录片",
    "media_documentary": "纪录片",
    "movie": "电影",
    "media_movie": "电影",
    "tv": "电视剧",
    "media_tv": "电视剧",
}


def bangumi_type_name(value: Optional[Any]) -> str:
    """把 Bangumi 条目类型数字转换为中文名。

    Args:
        value: ``type`` 字段值（1/2/3/4/6）。

    Returns:
        中文类型名（动画/三次元…），无法识别时为空字符串。
    """
    type_id = to_int(value)
    if type_id is None:
        return ""
    return _BANGUMI_TYPE_NAMES.get(type_id, "")


def normalize_category(value: Optional[Any]) -> str:
    """把接口返回的类别字段转换为展示用中文名。

    兼容中文名（“动画”“番剧”）与英文代号（“media_bangumi”等）。

    Args:
        value: 原始类别值。

    Returns:
        规范化后的类别名，无法识别时返回空字符串。
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    # Bangumi 数字类型优先转换；其余走别名 / 原文
    type_name = bangumi_type_name(raw)
    if type_name:
        return type_name
    return _CATEGORY_ALIASES.get(raw, raw)


def _dig(data: Dict[str, Any], *keys: str) -> Any:
    """在字典中按 ``a.b.c`` 形式的点号路径安全取值。

    Args:
        data: 目标字典。
        *keys: 候选路径，按顺序尝试，返回第一个能取到的值。

    Returns:
        命中的值；找不到返回 None。
    """
    if not isinstance(data, dict):
        return None
    for key in keys:
        if not key:
            continue
        node: Any = data
        ok: bool = True
        for part in str(key).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok:
            return node
    return None


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """把各种形态的值转换为 int，转换失败返回 default。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value) if default is None else default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """把各种形态的值转换为 float（支持 "9.8" / 9.8 / 9），失败返回 default。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def format_time(value: Any) -> str:
    """把时间戳或时间字符串统一格式化为 ``YYYY-MM-DD HH:MM``。

    Args:
        value: 秒级时间戳(int/float/字符串数字) 或形如 "2020-10-03" 的日期字符串。

    Returns:
        格式化后的时间字符串；无法解析时返回空字符串。
    """
    if value is None or value == "":
        return ""
    # 纯数字 -> 时间戳
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        try:
            return datetime.fromtimestamp(numeric).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    # 只保留前 16 位（YYYY-MM-DD HH:MM）
    return text[:16] if len(text) >= 16 else text


def names_of(value: Any) -> List[str]:
    """把“地区/标签/类型”等字段解析为字符串列表。

    兼容多种返回形态：
    - ``["日本", "热血"]``
    - ``[{"name": "日本", "count": 12}]``（Bangumi tags）
    - ``{"name": "日本"}``
    - 单个字符串 ``"日本"``

    Args:
        value: 原始字段值。

    Returns:
        名称字符串列表。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        name = value.get("name") or value.get("title")
        return [str(name)] if name else []
    if isinstance(value, Iterable):
        result: List[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("title")
                if name:
                    result.append(str(name))
        return result
    return []


def derive_status(data: Dict[str, Any]) -> Optional[str]:
    """根据原始数据推断播出状态。

    Bangumi 不直接提供“连载/完结”状态字段；此函数保留了对旧式字段
    （``is_finish`` / ``is_started`` 等）的兼容推断，缺失时返回 None
    （界面将显示为 “—”）。

    Args:
        data: 条目或详情的原始字典。

    Returns:
        "已完结" / "连载中" / "未开播"，信息不足返回 None。
    """
    if not isinstance(data, dict):
        return None
    finished = to_int(_dig(data, "is_finish", "finish"))
    if finished == 1:
        return "已完结"
    started = to_int(_dig(data, "is_started"))
    if started == 1:
        return "连载中"
    if started == 0:
        return "未开播"
    return None


# 表示“完结”的 infobox 键
_END_MARKERS = ("放送结束", "播出结束", "播放结束", "已完结", "连载结束")

# 推断“连载中”的开播距今最大天数（保守，避免把老作品标错为连载）
_AIRING_WINDOW_DAYS: int = 210


def _start_date_of(data: Dict[str, Any]) -> Optional[datetime]:
    """取条目开播/上映日期（顶层 date/air_date 字段）。"""
    value = _dig(data, "date", "air_date")
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def derive_subject_status(data: Dict[str, Any]) -> Optional[str]:
    """按 Bangumi 数据推断播出状态（搜索结果/详情通用）。

    规则（尽力而为，不做错误断言）：
    1. 先兼容旧式状态字段（``derive_status``）；
    2. infobox 含“放送结束/播出结束/播放结束” -> “已完结”；
    3. 动画（type=2）且开播日期在最近约 210 天内且无完结信息
       -> 保守标为“连载中”；
    4. 其余情况返回 None（界面显示 “—”）。

    Args:
        data: Bangumi Subject 原始字典。

    Returns:
        "已完结" / "连载中"，无法可靠判断时返回 None。
    """
    if not isinstance(data, dict):
        return None
    legacy = derive_status(data)
    if legacy:
        return legacy

    infobox_keys = [key for key, _ in subject_infobox_rows(data)]
    if any(key in infobox_keys for key in _END_MARKERS):
        return "已完结"

    if to_int(_dig(data, "type")) != 2:
        return None
    start = _start_date_of(data)
    if start is None:
        return None
    days = (datetime.now() - start).days
    if 0 <= days <= _AIRING_WINDOW_DAYS:
        return "连载中"
    return None


def _rating_of(item: Dict[str, Any]) -> Tuple[Optional[float], Optional[int]]:
    """从条目中提取 (评分, 评分人数)。

    兼容 Bangumi：``rating.score`` + ``rating.total``（或 ``rating.count``）。

    Args:
        item: 原始条目字典。

    Returns:
        (score, count) 元组，缺省为 (None, None)。
    """
    score: Optional[float] = to_float(
        _dig(item, "rating.score", "rating.rate", "score")
    )
    count: Optional[int] = to_int(
        _dig(item, "rating.total", "rating.count", "rating.people")
    )
    return score, count


# ---------------------------------------------------------------------------
# Bangumi 条目字段映射辅助
# ---------------------------------------------------------------------------


def subject_title(data: Dict[str, Any]) -> str:
    """条目标题：优先中文名 name_cn，其次原名 name（跳过空值）。"""
    if not isinstance(data, dict):
        return ""
    for key in ("name_cn", "name", "title", "original_title"):
        text = clean_text(data.get(key))
        if text:
            return text
    return ""


def subject_cover(data: Dict[str, Any]) -> str:
    """封面图 URL：优先 images.large/common/medium，其次顶层 image。"""
    images = data.get("images") if isinstance(data, dict) else None
    if isinstance(images, dict):
        for key in ("large", "common", "medium"):
            url = images.get(key)
            if url:
                return str(url)
    image = data.get("image") if isinstance(data, dict) else None
    return str(image or "")


def subject_tags(data: Dict[str, Any]) -> List[str]:
    """条目标签（Bangumi ``tags`` 数组 -> 名称列表）。"""
    return names_of(data.get("tags") if isinstance(data, dict) else None)


def _infobox_values(value: Any) -> List[str]:
    """把 Bangumi infobox 的 value 展开为字符串列表。

    value 形态可能是字符串，或 ``[{k,v}, {v}]`` 等数组，或对象 ``{v}``。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        # {"v": "..."} / {"k": "ja", "v": "..."} 等
        inner = value.get("v")
        if inner:
            return _infobox_values(inner)
        return []
    if isinstance(value, Iterable):
        result: List[str] = []
        for entry in value:
            result.extend(_infobox_values(entry))
        return result
    return []


def subject_infobox_rows(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """解析 Bangumi ``infobox`` 为 (键, 值字符串) 列表。

    Args:
        data: 条目字典（含 infobox 数组）。

    Returns:
        键值对列表；infobox 缺失或格式异常时为空列表。
    """
    infobox = data.get("infobox") if isinstance(data, dict) else None
    if not isinstance(infobox, list):
        return []
    rows: List[Tuple[str, str]] = []
    for entry in infobox:
        if not isinstance(entry, dict):
            continue
        key = clean_text(entry.get("key"))
        values = [clean_text(v) for v in _infobox_values(entry.get("value"))]
        values = [v for v in values if v]
        if key and values:
            rows.append((key, " / ".join(values)))
    return rows


# 常见地区标签名（Bangumi 的 tags / meta_tags 可能直接使用）
_REGION_NAMES = {
    "日本", "中国", "中国大陆", "中国香港", "中国澳门", "中国台湾",
    "香港", "澳门", "台湾", "美国", "欧美", "韩国", "英国", "法国",
    "德国", "泰国", "新加坡", "意大利", "西班牙", "加拿大", "澳大利亚",
    "俄罗斯", "其他",
}


def _dedupe(items: Iterable[str]) -> List[str]:
    """去重并保持顺序。"""
    seen: set = set()
    result: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def subject_areas(data: Dict[str, Any]) -> List[str]:
    """地区解析（真实 Bangumi 字段说明见模块 docstring）。

    优先级：
    1. infobox 的“地区 / 国家/地区”条目；
    2. ``tags`` / ``meta_tags`` 中命中地区词表（日本/中国/美国/欧美…）的标签。
    Bangumi 详情通常没有 infobox“地区”键，地区信息主要位于 tags。
    """
    if not isinstance(data, dict):
        return []
    infobox_hits: List[str] = []
    for key, value in subject_infobox_rows(data):
        if key in ("地区", "国家/地区", "国家或地区", "region", "area"):
            infobox_hits = [v.strip() for v in value.split(" / ") if v.strip()]
            break
    if infobox_hits:
        return _dedupe(infobox_hits)

    candidates: List[str] = subject_tags(data)
    meta_tags = data.get("meta_tags")
    if isinstance(meta_tags, list):
        for item in meta_tags:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict) and item.get("name"):
                candidates.append(str(item["name"]))
    tag_hits = [name for name in candidates if name in _REGION_NAMES]
    if tag_hits:
        return _dedupe(tag_hits)

    # 兼容旧数据：顶层 areas 字段
    return _dedupe(names_of(data.get("areas")))


# 常见的“制作团队 / 创作人员”信息键
_PRODUCTION_KEYS = (
    "原作",
    "原案",
    "导演",
    "监督",
    "总监督",
    "系列构成",
    "脚本",
    "编剧",
    "音乐",
    "人物设定",
    "角色设计",
    "总作画监督",
    "作画监督",
    "动画制作",
    "制作",
    "出品",
    "企划",
    "美术",
    "色彩设计",
    "摄影",
    "音响监督",
    "发行",
)


def subject_staff(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """从 infobox 中提取制作团队（职位, 姓名）列表。"""
    staff: List[Tuple[str, str]] = []
    for key, value in subject_infobox_rows(data):
        if key in _PRODUCTION_KEYS:
            staff.append((key, value))
    return staff


def subject_eps_count(data: Dict[str, Any]) -> Optional[int]:
    """总集数：``eps`` 为数字时直接用；为数组时取长度。"""
    eps = _dig(data, "eps", "total_episodes")
    if isinstance(eps, list):
        return len(eps)
    return to_int(eps)


def subject_collection_total(data: Dict[str, Any]) -> Optional[int]:
    """收藏数：``collection_total`` / ``collects``，否则累加 ``collection.*``。"""
    total = to_int(_dig(data, "collection_total", "collects"))
    if total is not None:
        return total
    collection = data.get("collection")
    if isinstance(collection, dict):
        parts = [
            to_int(collection.get(k))
            for k in ("wish", "collect", "doing", "on_hold", "dropped")
        ]
        valid = [p for p in parts if p is not None]
        if valid:
            return sum(valid)
    return None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class SearchItem:
    """搜索 / 索引返回的单个条目。"""

    season_id: Optional[int]
    """Bangumi 条目 ID（映射自 subject.id）。"""
    media_id: Optional[int]
    """兼容字段：Bangumi 无 media_id，恒为 None。"""
    title: str
    """标题（name_cn 或 name）。"""
    category: str = ""
    """类别名（动画 / 三次元…）。"""
    styles: List[str] = field(default_factory=list)
    """标签列表（tags）。"""
    areas: List[str] = field(default_factory=list)
    """地区列表（infobox 地区）。"""
    status: Optional[str] = None
    """播出状态（Bangumi 无该数据时为 None，界面显示 “—”）。"""
    score: Optional[float] = None
    """评分（rating.score，0-10，可能为 None）。"""
    score_count: Optional[int] = None
    """评分人数（rating.total）。"""
    update_desc: str = ""
    """更新信息描述（Bangumi 无每周更新进度，仅在有总集数时提示）。"""
    pub_time: str = ""
    """首播 / 上映时间（date / air_date）。"""
    cover: str = ""
    """封面图 URL。"""
    evaluate: str = ""
    """简介（summary）。"""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchItem":
        """从 Bangumi 搜索接口的原始条目构造对象。

        Args:
            data: Bangumi Subject 原始字典。

        Returns:
            SearchItem 实例。
        """
        score, count = _rating_of(data)
        update_desc = ""
        ep_count = subject_eps_count(data)
        if ep_count is not None:
            update_desc = f"共 {ep_count} 话"
        return cls(
            season_id=to_int(_dig(data, "id", "subject_id")),
            media_id=to_int(_dig(data, "media_id")),
            title=subject_title(data),
            category=normalize_category(data.get("type")),
            styles=subject_tags(data),
            areas=subject_areas(data),
            status=derive_subject_status(data),
            score=score,
            score_count=count,
            update_desc=update_desc,
            pub_time=format_time(_dig(data, "date", "air_date", "pub_time")),
            cover=subject_cover(data),
            evaluate=clean_text(_dig(data, "summary", "evaluate")),
        )


@dataclass
class SearchPage:
    """一页搜索结果的容器。"""

    items: List[SearchItem]
    page: int
    page_size: int
    total_results: int = 0
    total_pages: int = 1

    @property
    def empty(self) -> bool:
        """当前页是否没有数据。"""
        return len(self.items) == 0


@dataclass
class EpisodeInfo:
    """分集信息（Bangumi Episode）。"""

    ep_id: Optional[int]
    """分集 ID（episode.id）。"""
    title: str
    """分集短标题：优先 name_cn/name，缺失时用集数（如 "1"）。"""
    long_title: str
    """分集完整标题（name_cn / name）。"""
    pub_time: str
    """播出日期（airdate）。"""
    cover: str = ""

    @property
    def display_title(self) -> str:
        """优先返回完整标题，其次返回短标题。"""
        return self.long_title or self.title

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodeInfo":
        """从 Bangumi 分集原始字典构造对象。

        Args:
            data: Bangumi Episode 原始字典（``id/name/name_cn/ep/airdate/...``）。
        """
        name = clean_text(_dig(data, "name_cn", "name", "title"))
        ep_no = to_int(_dig(data, "ep", "sort"))
        if not name and ep_no is not None:
            name = f"第 {ep_no} 话"
        elif not name:
            name = ""
        return cls(
            ep_id=to_int(_dig(data, "id", "ep_id")),
            title=clean_text(_dig(data, "ep")) or name,
            long_title=name,
            pub_time=format_time(_dig(data, "airdate", "pub_time", "air_date")),
            cover=str(data.get("cover") or ""),
        )


@dataclass
class StaffItem:
    """制作人员 / 声优条目。"""

    role: str
    """职位（如“原作”“导演”）或角色名。"""
    name: str
    """人员姓名。"""


@dataclass
class SeasonDetail:
    """条目详情（Bangumi Subject 详情）。"""

    season_id: Optional[int]
    """Bangumi 条目 ID（映射自 subject.id）。"""
    media_id: Optional[int]
    """兼容字段：Bangumi 无 media_id，恒为 None。"""
    title: str
    """标题。"""
    original_title: str = ""
    """原名（name，与 name_cn 不同时才有意义）。"""
    cover: str = ""
    """封面图 URL。"""
    category: str = ""
    """类别名。"""
    status: Optional[str] = None
    """播出状态（Bangumi 无该数据时为 None）。"""
    areas: List[str] = field(default_factory=list)
    """地区。"""
    styles: List[str] = field(default_factory=list)
    """标签。"""
    pub_time: str = ""
    """开播 / 上映时间。"""
    episode_total: Optional[int] = None
    """总集数（可能未定）。"""
    newest_ep_desc: str = ""
    """最新一话描述（Bangumi 详情无该数据，通常为空）。"""
    score: Optional[float] = None
    """评分（rating.score）。"""
    score_count: Optional[int] = None
    """评分人数（rating.total）。"""
    views: Optional[int] = None
    """播放量（Bangumi 无该数据，恒为 None）。"""
    follows: Optional[int] = None
    """收藏人数（collection_total / collects）。"""
    evaluate: str = ""
    """简介 / 剧情介绍（summary）。"""
    casts: List[StaffItem] = field(default_factory=list)
    """主要声优（Bangumi v0 条目详情不含声优表，通常为空）。"""
    staff: List[StaffItem] = field(default_factory=list)
    """制作团队（从 infobox 中提取的原作 / 导演 / 音乐等）。"""
    episodes: List[EpisodeInfo] = field(default_factory=list)
    """分集列表（来自 /v0/subjects/{id}/episodes）。"""
    share_url: str = ""
    """条目页链接（https://bgm.tv/subject/{id}）。"""


@dataclass
class RankItem:
    """排行榜条目（Bangumi rank 排序结果）。"""

    rank: int
    """名次（从 1 开始，界面按返回顺序编号）。"""
    season_id: Optional[int]
    """Bangumi 条目 ID。"""
    title: str
    """标题。"""
    category: str = ""
    """类别名。"""
    score: Optional[float] = None
    """评分（rating.score，可能缺失）。"""
    heat_value: Optional[float] = None
    """热度数值：收藏数（collection_total / collects），缺失时为 None。"""
    heat_kind: str = ""
    """热度数值的含义（默认 “收藏”）。"""
    pub_time: str = ""
    """播出时间。"""
    cover: str = ""

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        rank: int,
        heat_kind_default: str = "收藏",
    ) -> "RankItem":
        """从 Bangumi 排序搜索结果原始条目构造对象。

        Args:
            data: Bangumi Subject 原始字典。
            rank: 该条目在榜单中的名次（从 1 开始）。
            heat_kind_default: 收藏数字段缺失时展示用的含义名。
        """
        score, _ = _rating_of(data)
        heat_kind = heat_kind_default
        heat_value: Optional[float] = None
        candidates: List[Tuple[str, str]] = [
            ("collection_total", "收藏"),
            ("collects", "收藏"),
            ("collection.collect", "收藏"),
            ("rating.total", "评分人数"),
        ]
        for path, kind in candidates:
            value = to_float(_dig(data, path))
            if value is not None:
                heat_value = value
                heat_kind = kind
                break
        return cls(
            rank=rank,
            season_id=to_int(_dig(data, "id", "subject_id")),
            title=subject_title(data),
            category=normalize_category(data.get("type")),
            score=score,
            heat_value=heat_value,
            heat_kind=heat_kind,
            pub_time=format_time(_dig(data, "date", "air_date", "pub_time")),
            cover=subject_cover(data),
        )


@dataclass
class TimelineItem:
    """追番日历（Bangumi 每周放送表）中的单个条目。"""

    season_id: Optional[int]
    """Bangumi 条目 ID。"""
    title: str
    """标题。"""
    cover: str = ""
    """封面。"""
    pub_time: str = ""
    """播出 / 上映时间。"""
    ep_label: str = ""
    """更新描述（Bangumi 日历仅提供“每周某日更新”的语义，通常为空）。"""
    category: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimelineItem":
        """从 Bangumi 日历条目的 Subject 原始字典构造对象。"""
        return cls(
            season_id=to_int(_dig(data, "id", "subject_id")),
            title=subject_title(data),
            cover=subject_cover(data),
            pub_time=format_time(_dig(data, "date", "air_date", "pub_time")),
            ep_label="",
            category=normalize_category(data.get("type")),
        )


@dataclass
class TimelineDay:
    """某一天（星期几）更新的条目集合。"""

    date: str
    """日期，形如 2025-01-05；Bangumi 日历无具体日期，通常为空。"""
    weekday: int
    """星期几，1=周一 … 7=周日。"""
    weekday_cn: str
    """星期中文名。"""
    is_today: bool
    """是否为今天。"""
    items: List[TimelineItem] = field(default_factory=list)
