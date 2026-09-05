"""离线测试夹具：模拟 Bangumi API v0 的返回结构。

由于开发环境无法访问 api.bgm.tv，这里按公开 API 文档记载的结构构造样本
数据，用于对解析、展示与交互逻辑做离线验证。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 搜索接口（POST /v0/search/subjects）响应
# ---------------------------------------------------------------------------

SEARCH_RESULT_DATA: Dict[str, Any] = {
    "total": 58,
    "limit": 10,
    "offset": 0,
    "data": [
        {
            "id": 33346,
            "type": 2,  # 动画
            "name": "ソードアート・オンライン",
            "name_cn": "刀剑神域",
            "summary": "2022年，人类实现了现实世界与虚拟世界的融合……",
            "date": "2012-07-08",
            "image": "http://lain.bgm.tv/pic/cover/l/6e/e1/33346_l.jpg",
            "images": {
                "large": "http://lain.bgm.tv/pic/cover/l/6e/e1/33346_l.jpg",
                "common": "http://lain.bgm.tv/pic/cover/c/6e/e1/33346_c.jpg",
            },
            "platform": "TV",
            "eps": 25,
            "volumes": 0,
            "infobox": [
                {"key": "话数", "value": "25"},
                {"key": "放送开始", "value": "2012年7月8日"},
                {"key": "地区", "value": "日本"},
                {"key": "原作", "value": "川原砾"},
            ],
            "rating": {"rank": 120, "total": 12345, "score": 8.8},
            "tags": [
                {"name": "战斗", "count": 1234},
                {"name": "奇幻", "count": 999},
            ],
            "collects": 2345678,
        },
        {
            "id": 8,
            "type": 2,
            "name": "鬼滅の刃",
            "name_cn": "鬼灭之刃",
            "summary": "大正时代，少年炭治郎的家人被鬼杀害……",
            "date": "2019-04-06",
            "image": "",
            "platform": "TV",
            "rating": {"rank": 30, "total": 90000, "score": 8.7},
            "tags": [{"name": "热血", "count": 1}],
            "collects": 654321,
        },
        {
            "id": 3001,
            "type": 6,  # 三次元（电影）
            "name": "Your Name.",
            "name_cn": "你的名字。",
            # 缺少 rating / images / eps，用于验证缺省容错
            "date": "2016-08-26",
            "infobox": [{"key": "地区", "value": "日本"}],
        },
    ],
}

SEARCH_EMPTY_DATA: Dict[str, Any] = {"total": 0, "limit": 10, "offset": 0, "data": []}

# ---------------------------------------------------------------------------
# 条目详情（GET /v0/subjects/{id}）
# ---------------------------------------------------------------------------

SUBJECT_DETAIL_DATA: Dict[str, Any] = {
    "id": 33346,
    "type": 2,
    "name": "ソードアート・オンライン",
    "name_cn": "刀剑神域",
    "summary": "2022年，人类实现了现实世界与虚拟世界的融合，一款VRMMORPG……",
    "nsfw": False,
    "date": "2012-07-08",
    "image": "http://lain.bgm.tv/pic/cover/l/6e/e1/33346_l.jpg",
    "images": {
        "large": "http://lain.bgm.tv/pic/cover/l/6e/e1/33346_l.jpg",
        "common": "http://lain.bgm.tv/pic/cover/c/6e/e1/33346_c.jpg",
    },
    "platform": "TV",
    "eps": 25,
    "total_episodes": 25,
    "volumes": 0,
    "infobox": [
        {"key": "话数", "value": "25"},
        {"key": "放送开始", "value": "2012年7月8日"},
        {"key": "地区", "value": "日本"},
        {"key": "语言", "value": "日语"},
        {"key": "原作", "value": "川原砾"},
        {"key": "导演", "value": "伊藤智彦"},
        {"key": "音乐", "value": "梶浦由記"},
        {"key": "动画制作", "value": "A-1 Pictures"},
        {"key": "放送结束", "value": "2012年12月23日"},
    ],
    "rating": {
        "rank": 120,
        "total": 12345,
        "count": {"1": 1, "2": 2, "3": 3},
        "score": 8.8,
    },
    "collection": {
        "wish": 100,
        "collect": 200,
        "doing": 50,
        "on_hold": 10,
        "dropped": 5,
    },
    "tags": [
        {"name": "战斗", "count": 1234},
        {"name": "奇幻", "count": 999},
    ],
}

# 分集列表（GET /v0/episodes?subject_id=…）
EPISODES_RESPONSE: Dict[str, Any] = {
    "total": 2,
    "limit": 100,
    "offset": 0,
    "data": [
        {
            "id": 33171,
            "type": 0,
            "name": "",
            "name_cn": "剑的世界",
            "duration": "24m",
            "airdate": "2012-07-08",
            "ep": 1,
            "sort": 1,
        },
        {
            "id": 33172,
            "type": 0,
            "name": "",
            "name_cn": "封弊者",
            "duration": "24m",
            "airdate": "2012-07-15",
            "ep": 2,
            "sort": 2,
        },
    ],
}

# ---------------------------------------------------------------------------
# 角色/声优（GET /v0/subjects/{id}/characters，直接返回数组）
# ---------------------------------------------------------------------------
CHARACTERS_RESPONSE: List[Dict[str, Any]] = [
    {
        "id": 16489,
        "name": "キリト / 桐谷和人",
        "relation": "主角",
        "actors": [{"id": 5764, "name": "松冈祯丞"}],
    },
    {
        "id": 16490,
        "name": "アスナ / 结城明日奈",
        "relation": "主角",
        "actors": [{"id": 4856, "name": "户松遥"}],
    },
    {
        "id": 99999,
        "name": "无配音角色",
        "relation": "配角",
        "actors": [],
    },
]

# ---------------------------------------------------------------------------
# 排行榜（POST /v0/search/subjects，sort="heat"）响应中的 data
# ---------------------------------------------------------------------------

RANK_LIST_DATA: List[Dict[str, Any]] = [
    {
        "id": 1001,
        "type": 2,
        "name": "Rank One",
        "name_cn": "榜首作品",
        "date": "2023-04-01",
        "rating": {"rank": 1, "total": 50000, "score": 9.8},
        "collects": 9876543,
    },
    {
        "id": 1002,
        "type": 2,
        "name": "Second",
        "name_cn": "第二作品",
        "rating": {"rank": 2, "total": 40000, "score": 9.5},
        "collects": 7654321,
    },
    {
        "id": 1003,
        "type": 2,
        "name": "Third",
        "name_cn": "第三作品",
        # 无评分 / 收藏：验证缺省容错
    },
]

# 排行榜接口完整响应
RANK_RESPONSE_DATA: Dict[str, Any] = {
    "total": 3,
    "limit": 25,
    "offset": 0,
    "data": RANK_LIST_DATA,
}

# ---------------------------------------------------------------------------
# 追番日历（GET /v0/calendar）
# ---------------------------------------------------------------------------

CALENDAR_DATA: List[Dict[str, Any]] = [
    {
        "weekday": {"en": "Mon", "cn": "周一", "ja": "月", "id": 1},
        "items": [
            {
                "id": 5001,
                "type": 2,
                "name": "Weekly Anime A",
                "name_cn": "周一更新作品",
                "date": "2025-01-06",
            }
        ],
    },
    {
        "weekday": {"en": "Tue", "cn": "周二", "ja": "火", "id": 2},
        "items": [
            {
                "id": 5002,
                "type": 2,
                "name": "Weekly Anime B",
                "name_cn": "周二更新作品",
            }
        ],
    },
    {
        "weekday": {"en": "Sun", "cn": "周日", "ja": "日", "id": 0},
        "items": [
            {
                "id": 5000,
                "type": 2,
                "name": "Weekly Anime Sun",
                "name_cn": "周日更新作品",
            }
        ],
    },
]

# ---------------------------------------------------------------------------
# 兼容别名（历史测试变量名，避免遗漏引用直接报错）
# ---------------------------------------------------------------------------
SEASON_VIEW_DATA: Dict[str, Any] = SUBJECT_DETAIL_DATA
TIMELINE_DATA: List[Dict[str, Any]] = CALENDAR_DATA
