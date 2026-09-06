"""配置文件（可选模块）。

集中管理工具的静态配置：应用信息、分页大小、番剧类型映射、
排行榜类别、时间线类型、Bangumi API 端点与网络参数等。

注意事项：
- 数据层默认匿名调用 Bangumi 公开接口（https://api.bgm.tv/v0），无需登录；
- 若运行环境到 Bangumi 网络不佳（如连接超时），可设置代理环境变量
  ``BANGUMI_PROXY``（如 http://127.0.0.1:7890），
  程序启动时自动应用到 requests 请求会话上；
- 更多网络参数（超时、重试、SSL、API 基址）见文件底部“Bangumi API 网络设置”段。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 应用信息
# ---------------------------------------------------------------------------
# 界面显示的应用名/版本号【唯一来源】：GUI 窗口标题、rich 横幅、退出语、
# 模块元数据均引用这里。以后如需改名称/版本，只需修改本段。
APP_NAME: str = "Bangumi 番剧数据查询工具"
APP_NAME_EN: str = "Bangumi Anime Query"
VERSION: str = "3.2.0"

# 调试模式：True 时界面会打印异常堆栈，便于排错
DEBUG: bool = os.environ.get("BANGUMI_DEBUG", "0") == "1"

# ---------------------------------------------------------------------------
# 分页 / 列表容量
# ---------------------------------------------------------------------------
SEARCH_PAGE_SIZE: int = 10          # 番剧搜索每页条数
INDEX_PAGE_SIZE: int = 30           # 番剧索引每页条数（保留）
RANK_LIMIT: int = 50                # 排行榜最多展示条数（界面分页展示）
RANK_PAGE_SIZE: int = 10            # 排行榜界面每页条数（与搜索结果一致可翻页）
RANK_POOL_SIZE: int = 150           # 榜单候选池大小（从 /v0/subjects 抓取后再本地排序）
EPISODE_DISPLAY_LIMIT: int = 80     # 详情里分集列表最多展示条数(超出截断提示)

# 排行榜“第二重筛选”排序维度（对应客户端按指标排序）
RANK_SORT_KEYS: List[str] = ["热度", "评分", "收藏数"]

# ---------------------------------------------------------------------------
# 番剧类型（菜单 season_type 取值，历史沿用）
# ---------------------------------------------------------------------------
SEASON_TYPE_ANIME: int = 1          # 番剧(主要为日本动画)
SEASON_TYPE_MOVIE: int = 2          # 电影
SEASON_TYPE_DOCUMENTARY: int = 3    # 纪录片
SEASON_TYPE_GUOCHUANG: int = 4      # 国创(国产动画)

# 番剧类型英文/数字 -> 中文名（用于展示）
SEASON_TYPE_NAMES: Dict[Any, str] = {
    SEASON_TYPE_ANIME: "番剧",
    SEASON_TYPE_MOVIE: "电影",
    SEASON_TYPE_DOCUMENTARY: "纪录片",
    SEASON_TYPE_GUOCHUANG: "国创",
    "1": "番剧",
    "2": "电影",
    "3": "纪录片",
    "4": "国创",
}

# ---------------------------------------------------------------------------
# 排行榜类别（菜单顺序展示）
# ---------------------------------------------------------------------------
# season_types: 该类别对应的官方榜 season_type 列表（“全部”会合并多个榜单）
# meta: 当官方排行榜接口不可用时，退化为番剧索引排序所使用的过滤器类型
RANK_CATEGORIES: List[Dict[str, Any]] = [
    {
        "label": "全部（番剧+国创+纪录片）",
        "short": "全部",
        "season_types": (1, 4, 3),
        "meta": "anime",
    },
    {
        "label": "日漫（番剧榜）",
        "short": "日漫",
        "season_types": (1,),
        "meta": "anime",
    },
    {
        "label": "国漫（国创榜）",
        "short": "国漫",
        "season_types": (4,),
        "meta": "guochuang",
    },
    {
        "label": "纪录片",
        "short": "纪录片",
        "season_types": (3,),
        "meta": "documentary",
    },
    {
        "label": "电影",
        "short": "电影",
        "season_types": (2,),
        "meta": "movie",
    },
]

# 排行榜官方接口统计的“近 N 天”参数
RANK_DAY: int = 3

# ---------------------------------------------------------------------------
# 追番日历（时间线）
# ---------------------------------------------------------------------------
TIMELINE_TYPES: List[Dict[str, Any]] = [
    {"label": "番剧", "value": SEASON_TYPE_ANIME},
    {"label": "国创", "value": SEASON_TYPE_GUOCHUANG},
    {"label": "影视", "value": 3},
]

WEEKDAY_NAMES: List[str] = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ---------------------------------------------------------------------------
# Bangumi API 网络设置（网络加固，不改变任何 UI / 操作逻辑 / 功能）
# ---------------------------------------------------------------------------
# 数据源根地址：默认官方 v0 端点；可用环境变量 BANGUMI_API_BASE 覆盖
# （例如自设可达网关/镜像时，地址需包含 /v0 路径后缀）
BANGUMI_API_BASE: str = os.environ.get(
    "BANGUMI_API_BASE", "https://api.bgm.tv/v0"
).rstrip("/")

# 追番日历接口地址：注意 Bangumi 的日历在“根路径 /calendar”（不在 /v0 下），
# 用环境变量 BANGUMI_CALENDAR_URL 可覆盖（自设网关/镜像时按实际路径填写）
BANGUMI_CALENDAR_URL: str = os.environ.get(
    "BANGUMI_CALENDAR_URL", "https://api.bgm.tv/calendar"
).rstrip("/")

# 请求超时（秒）——连接超时与读取(响应)超时分开设置，避免网络不佳时整体“卡死”
CONNECT_TIMEOUT: float = float(os.environ.get("BANGUMI_CONNECT_TIMEOUT", "5.0"))
REQUEST_TIMEOUT: float = float(os.environ.get("BANGUMI_READ_TIMEOUT", "10.0"))

# 单次请求总尝试次数（>=1，含首次）：对连接/读取超时、HTTP 429、5xx 自动重试
REQUEST_RETRY_TIMES: int = max(1, int(os.environ.get("BANGUMI_RETRY_TIMES", "2")))

# 代理：显式代理优先取 BANGUMI_PROXY；
# 未设置时 requests 会信任系统环境变量 HTTP_PROXY / HTTPS_PROXY
PROXY: str = os.environ.get("BANGUMI_PROXY", "").strip()

# SSL 证书校验：默认开启（安全）。仅当确因本机证书链问题无法访问、
# 且用户知情同意时，设置环境变量 BANGUMI_INSECURE=1 才关闭校验
SSL_VERIFY: bool = os.environ.get("BANGUMI_INSECURE", "0") != "1"
