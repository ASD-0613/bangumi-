"""番剧数据查询工具 —— 数据层：Bangumi API v0 封装。

本模块对外提供的函数签名与数据类保持不变（供 cli_main / GUI / 测试复用），
仅将数据来源由 bilibili 替换为 Bangumi：

- 搜索番剧：``POST /v0/search/subjects``（按关键词 + 类型过滤，sort=match）
- 番剧详情：``GET  /v0/subjects/{id}``（分集另取
  ``GET /v0/episodes?subject_id={id}``）
- 排行榜：拉取 ``GET /v0/subjects?type=…&sort=rank&limit=&offset=`` 的
  可翻页候选池（每页 25，可真正翻页），再按所选维度（热度/评分/收藏数）
  本地排序取前 50（见排行榜实现说明）
- 本周更新日历：``GET https://api.bgm.tv/calendar``（按星期分组；
  注意该接口位于站点根路径，不属于 ``/v0`` 前缀）

请求约定（网络加固）：
- 基础地址默认 ``https://api.bgm.tv/v0``（可用环境变量 ``BANGUMI_API_BASE``
  覆盖），所有请求带 ``User-Agent: <应用名>/<版本号>`` 请求头
  （Bangumi 要求 UA 否则返回 403）；
- 分页使用 ``limit + offset``（搜索接口响应含 ``total/limit/offset/data``）；
- 超时分开设置：连接超时 config.CONNECT_TIMEOUT（默认 5s）、读取超时
  config.REQUEST_TIMEOUT（默认 10s），避免网络不佳时整体“卡死”；
- 自动重试：连接/读取超时、HTTP 429、5xx 会按 config.REQUEST_RETRY_TIMES
  自动重试（最多 2 次尝试），重试间短暂退避；
- 代理配置：config.PROXY（环境变量 BANGUMI_PROXY / BILIBILI_PROXY）由
  ``apply_network_settings()`` 应用到 requests 会话；未设置时信任系统代理；
  仅在 DEBUG 模式或首次请求时打印一行 “[代理] 使用代理: …”；
- SSL 校验默认开启，可通过 ``BANGUMI_INSECURE=1`` 关闭（仅在证书问题场景）。

注意：
- 数据映射细节（如 name_cn->标题、rating.score->评分等）见
  ``models/bangumi.py`` 中各 ``from_*`` 工厂方法；
- Bangumi 不提供“连载/完结”与“播放量/最新一话”等 B 站专有字段，
  对应模型字段解析后为 None / 空，界面会显示 “—” 占位。
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import config
from ..models.bangumi import (
    EpisodeInfo,
    RankItem,
    SearchItem,
    SearchPage,
    SeasonDetail,
    StaffItem,
    TimelineDay,
    TimelineItem,
    _dig,
    _rating_of,
    bangumi_type_name,
    clean_text,
    derive_subject_status,
    format_time,
    subject_areas,
    subject_collection_total,
    subject_cover,
    subject_eps_count,
    subject_staff,
    subject_tags,
    subject_title,
    to_float,
    to_int,
)

__all__ = [
    "BangumiQueryError",
    "apply_network_settings",
    "describe_error",
    "search_bangumi",
    "parse_search_page",
    "get_season_detail",
    "parse_season_view",
    "fetch_ranking",
    "parse_rank_list",
    "fetch_timeline",
    "parse_timeline",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BANGUMI_API_BASE: str = config.BANGUMI_API_BASE

# Bangumi 要求 User-Agent 形如 “应用名/版本号”
_USER_AGENT = f"BilibiliBangumiQuery/{config.VERSION}"

# 搜索/详情默认覆盖的条目类型：2=动画、6=三次元
_SUBJECT_TYPES: List[int] = [2, 6]

# 连接 / 读取超时（秒）与总尝试次数（网络加固，见 config）
_CONNECT_TIMEOUT: float = config.CONNECT_TIMEOUT
_READ_TIMEOUT: float = config.REQUEST_TIMEOUT
_ATTEMPTS: int = config.REQUEST_RETRY_TIMES

# 重试退避间隔（秒），按“第几次重试”取值
_RETRY_DELAYS: List[float] = [0.5, 1.0, 2.0]


# ---------------------------------------------------------------------------
# 异常与错误提示
# ---------------------------------------------------------------------------


class BangumiQueryError(Exception):
    """业务查询错误，携带可直接展示给用户的中文信息。"""


class BangumiHTTPError(BangumiQueryError):
    """Bangumi API 返回非 2xx 状态码。"""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status
        self.message = message


class BangumiNetworkError(BangumiQueryError):
    """网络层错误（超时 / 连接失败 / 请求异常）。"""


_HTTP_CODE_HINTS: Dict[int, str] = {
    401: "需要登录或鉴权（HTTP 401）",
    403: "访问被拒绝（HTTP 403），请确认请求携带了合法的 User-Agent",
    404: "数据不存在或已被删除（HTTP 404）",
    429: "请求过于频繁（HTTP 429），请稍后重试",
}


def describe_error(exc: BaseException) -> str:
    """把各种异常转换为用户可读的中文提示。

    Args:
        exc: 捕获到的异常对象。

    Returns:
        一段可直接展示的中文描述。
    """
    if isinstance(exc, BangumiHTTPError):
        hint = _HTTP_CODE_HINTS.get(exc.status)
        if hint:
            detail = exc.message or ""
            return f"{hint}（{detail}）" if detail else hint
        if 500 <= exc.status < 600:
            return f"Bangumi 服务暂时不可用（HTTP {exc.status}），请稍后重试"
        return f"HTTP 请求失败（{exc.status}）：{exc.message or '无详情'}"
    if isinstance(exc, BangumiNetworkError):
        return (
            f"网络请求失败：{exc}。请检查网络连接；网络受限时可配置代理"
            "（环境变量 BANGUMI_PROXY 或 BILIBILI_PROXY）"
        )
    if isinstance(exc, BangumiQueryError):
        return str(exc)
    return f"未知错误：{exc!r}"


# ---------------------------------------------------------------------------
# 网络层
# ---------------------------------------------------------------------------

# 模块级会话：统一 User-Agent、连接池与请求头
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
)
# 关闭 urllib3 自带重试（避免尝试次数翻倍），统一由 _request 精确控制
_DEFAULT_RETRY = Retry(total=0, connect=0, read=0, redirect=0)
_SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=_DEFAULT_RETRY, pool_connections=16, pool_maxsize=16
    ),
)
_SESSION.mount(
    "http://",
    HTTPAdapter(
        max_retries=_DEFAULT_RETRY, pool_connections=16, pool_maxsize=16
    ),
)

# 代理信息只打印一次（此后仅在 DEBUG 模式下重复打印）
_PROXY_REPORTED: bool = False


def _report_proxy() -> None:
    """按需打印代理调试信息（不改界面，仅输出到控制台）。"""
    global _PROXY_REPORTED
    proxy = config.PROXY
    if not proxy:
        proxy = str(
            _SESSION.proxies.get("https") or _SESSION.proxies.get("http") or ""
        )
    if proxy and (config.DEBUG or not _PROXY_REPORTED):
        print(f"[代理] 使用代理: {proxy}", flush=True)
        _PROXY_REPORTED = True


def apply_network_settings() -> None:
    """把 config 中的代理配置应用到 requests 会话。

    代理配置沿用原有逻辑（config.PROXY，来自环境变量 BANGUMI_PROXY /
    BILIBILI_PROXY）；未设置代理时 requests 会信任系统环境代理
    （HTTP_PROXY / HTTPS_PROXY，trust_env 默认开启）。
    """
    if config.PROXY:
        proxies: Dict[str, str] = {"http": config.PROXY, "https": config.PROXY}
        _SESSION.proxies.update(proxies)


def _raise_for_response(resp: requests.Response) -> None:
    """根据 HTTP 状态码抛出对应业务异常。"""
    if resp.status_code == 200:
        return
    snippet = resp.text[:200] if resp.text else ""
    if resp.status_code == 404:
        raise BangumiHTTPError(404, "数据不存在或已被删除")
    if resp.status_code == 429:
        raise BangumiHTTPError(429, "请求过于频繁，请稍后重试")
    if resp.status_code == 403:
        raise BangumiHTTPError(403, "请确认 User-Agent 是否合法")
    raise BangumiHTTPError(resp.status_code, snippet)


def _request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    """发起请求并解析 JSON：超时/5xx/429 自动重试 + 统一异常处理。

    网络加固（不改变任何调用方接口）：
    - 连接超时与读取超时分开（见 config.CONNECT_TIMEOUT / REQUEST_TIMEOUT）；
    - 对连接超时、读取超时、HTTP 429、5xx 自动重试（总尝试次数
      config.REQUEST_RETRY_TIMES，默认最多 2 次，重试间短暂退避）；
    - SSL 校验开关 config.SSL_VERIFY；
    - 首次/DEBUG 时打印代理信息。

    Args:
        method: HTTP 方法（GET/POST）。
        url: 完整请求地址。
        params: URL 查询参数。
        body: JSON 请求体。

    Returns:
        解析后的 JSON 数据（dict / list 等）。

    Raises:
        BangumiNetworkError: 连接/读取超时、代理或 SSL 等网络问题。
        BangumiHTTPError: HTTP 状态码非 2xx（4xx 不重试，直接抛出）。
        BangumiQueryError: 返回内容无法解析为 JSON。
    """
    _report_proxy()
    attempts: int = max(1, _ATTEMPTS)
    last_error: Optional[BangumiQueryError] = None

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            delay = _RETRY_DELAYS[min(attempt - 2, len(_RETRY_DELAYS) - 1)]
            time.sleep(delay)
        try:
            resp: requests.Response = _SESSION.request(
                method,
                url,
                params=params,
                json=body,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                verify=config.SSL_VERIFY,
            )
        except requests.exceptions.ConnectTimeout as exc:
            # 连接建立阶段超时：通常可重试一次
            last_error = BangumiNetworkError(
                "无法连接到 Bangumi 服务器（连接超时）。"
                "请检查网络，或设置 BANGUMI_PROXY / BILIBILI_PROXY 代理后重试"
            )
            continue
        except requests.exceptions.ReadTimeout as exc:
            # 已建立连接但响应超时：重试一次看是否为瞬时抖动
            last_error = BangumiNetworkError(
                f"读取响应超时（>{_READ_TIMEOUT:g} 秒），服务器未及时返回。"
                "请稍后重试，或检查网络/代理"
            )
            continue
        except requests.exceptions.SSLError as exc:
            # 证书校验失败重试无意义，直接给出可操作提示
            raise BangumiNetworkError(
                "SSL 证书校验失败，无法建立安全连接。"
                "请更新系统 CA 证书；若确属证书链问题且知情同意，"
                "可设置 BANGUMI_INSECURE=1 后重试"
            ) from exc
        except requests.exceptions.ProxyError as exc:
            raise BangumiNetworkError(
                "代理连接失败。请检查代理配置"
                "（BANGUMI_PROXY / BILIBILI_PROXY / 系统代理）是否正确可用"
            ) from exc
        except requests.exceptions.RequestException as exc:
            # 其它网络异常（DNS、连接被拒等）
            last_error = BangumiNetworkError(
                f"{exc.__class__.__name__}: {exc}"
            )
            continue

        try:
            _raise_for_response(resp)
        except BangumiHTTPError as exc:
            # 429 与 5xx 可重试；其余 4xx（404/403 等）直接抛出
            if exc.status == 429 or 500 <= exc.status < 600:
                last_error = exc
                continue
            raise
        try:
            return resp.json()
        except ValueError as exc:
            raise BangumiQueryError(
                "接口返回内容不是合法 JSON，请稍后重试"
            ) from exc

    # 所有尝试均失败：抛出最后一次记录的异常
    assert last_error is not None, "重试循环后异常不应为空"
    raise last_error


def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """GET 请求便捷封装。"""
    return _request("GET", url, params=params)


def _post_json(url: str, body: Optional[Dict[str, Any]] = None) -> Any:
    """POST JSON 请求便捷封装。"""
    return _request("POST", url, body=body)


# ---------------------------------------------------------------------------
# 1. 搜索番剧
# ---------------------------------------------------------------------------


def _search(
    keyword: str,
    page: int = 1,
    page_size: int = 20,
    *,
    sort: str = "match",
    subject_types: Optional[Sequence[int]] = None,
    extra_tag: str = "",
    extra_filter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """调用 POST /v0/search/subjects 的同步实现。

    Args:
        keyword: 搜索关键词。
        page: 页码（从 1 开始）。
        page_size: 每页条数（建议 <=25）。
        sort: 排序方式，search=“match”、排行榜=“rank”。
        subject_types: 条目类型列表，如 [2]（动画）。
        extra_tag: 附加标签过滤（如“日本 / 中国”）。
        extra_filter: 附加的 filter 字段（覆盖默认构建结果）。

    Returns:
        接口返回的 JSON 字典。

    Raises:
        BangumiQueryError / BangumiHTTPError / BangumiNetworkError。
    """
    if sort == "rank":
        # 说明：POST /v0/search/subjects 的 sort=rank 空关键词只返回固定
        # 旧数据前 10，不适合榜单；真实榜单实现基于可翻页的
        # GET /v0/subjects?type=&sort=rank（见排行榜实现）。
        body: Dict[str, Any] = {"keyword": "", "sort": "rank"}
    elif sort == "heat":
        body = {"keyword": keyword, "sort": "heat"}
    else:
        body = {"keyword": keyword, "sort": "match"}
    if extra_filter is not None:
        body["filter"] = extra_filter
    else:
        body["filter"] = {
            "type": list(subject_types) if subject_types else _SUBJECT_TYPES
        }
        if extra_tag:
            body["filter"]["tag"] = [extra_tag]
    # 分页：limit + offset
    body["limit"] = page_size
    body["offset"] = (max(1, page) - 1) * page_size
    data = _post_json(f"{BANGUMI_API_BASE}/search/subjects", body)
    if not isinstance(data, dict):
        raise BangumiQueryError("搜索接口返回数据格式异常")
    return data


async def search_bangumi(
    keyword: str, page: int = 1, page_size: Optional[int] = None
) -> SearchPage:
    """按关键词搜索番剧 / 影视条目（Bangumi /v0/search/subjects）。

    Args:
        keyword: 搜索关键词。
        page: 页码，从 1 开始。
        page_size: 每页条数，缺省使用 config.SEARCH_PAGE_SIZE。

    Returns:
        一页搜索结果 SearchPage。

    Raises:
        BangumiQueryError: 关键词为空时。
        BangumiNetworkError / BangumiHTTPError: 网络或接口错误。
    """
    keyword = keyword.strip()
    if not keyword:
        raise BangumiQueryError("搜索关键词不能为空")
    size = page_size or config.SEARCH_PAGE_SIZE
    data = _search(keyword, page=page, page_size=size)
    return parse_search_page(data, page=page, page_size=size)


def parse_search_page(data: Any, page: int, page_size: int) -> SearchPage:
    """解析 Bangumi 搜索结果响应为 SearchPage。

    Args:
        data: ``/v0/search/subjects`` 返回的 JSON（含 ``total/limit/offset/data``）。
        page: 当前页码。
        page_size: 每页条数。

    Returns:
        SearchPage 对象（无数据时 items 为空）。
    """
    if not isinstance(data, dict):
        return SearchPage(items=[], page=page, page_size=page_size)
    raw_list = data.get("data") or data.get("list") or data.get("result")
    if not isinstance(raw_list, list):
        raw_list = []
    items: List[SearchItem] = [
        SearchItem.from_dict(item) for item in raw_list if isinstance(item, dict)
    ]
    total: Optional[int] = to_int(data.get("total") or data.get("total_results"))
    total_pages: Optional[int] = None
    if total is not None:
        total_pages = (
            max(1, math.ceil(total / page_size)) if page_size else 1
        )
    if total_pages is None:
        total_pages = 1
    return SearchPage(
        items=items,
        page=page,
        page_size=page_size,
        total_results=total or 0,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# 2. 番剧详情
# ---------------------------------------------------------------------------


def _parse_subject_basic(data: Any) -> SeasonDetail:
    """从 Bangumi Subject 字典解析除分集外的基础详情。"""
    if not isinstance(data, dict):
        return SeasonDetail(season_id=None, media_id=None, title="")

    subject_id: Optional[int] = to_int(_dig(data, "id"))
    title = subject_title(data)
    original = clean_text(data.get("name")) if data.get("name") else ""
    if original == title:
        original = ""

    score, count = _rating_of(data)
    ep_total = subject_eps_count(data)
    episodes_from_list: List[EpisodeInfo] = []
    if isinstance(data.get("eps"), list):
        # 个别响应形态：eps 直接为分集数组（仅取正片）
        episodes_from_list = [
            EpisodeInfo.from_dict(ep) for ep in _main_episode_entries(data["eps"])
        ]
        if ep_total is None:
            ep_total = len(episodes_from_list)

    staff = [
        StaffItem(role=role, name=name) for role, name in subject_staff(data)
    ]

    # 播出状态：见 models.derive_subject_status —— 能确认完结才显示
    # “已完结”；开播近期的保守标“连载中”；无法判断显示 “—”（不做臆测）。
    status: Optional[str] = derive_subject_status(data)

    return SeasonDetail(
        season_id=subject_id,
        media_id=None,
        title=title,
        original_title=original,
        cover=subject_cover(data),
        category=bangumi_type_name(data.get("type")),
        status=status,
        areas=subject_areas(data),
        styles=subject_tags(data),
        pub_time=format_time(_dig(data, "date", "air_date")),
        episode_total=ep_total,
        newest_ep_desc="",
        score=score,
        score_count=count,
        views=None,
        follows=subject_collection_total(data),
        evaluate=clean_text(_dig(data, "summary", "evaluate")),
        casts=[],  # Bangumi v0 条目详情不含声优表
        staff=staff,
        episodes=episodes_from_list,
        share_url=(
            f"https://bgm.tv/subject/{subject_id}" if subject_id else ""
        ),
    )


def _fetch_episodes(subject_id: int) -> Optional[Dict[str, Any]]:
    """尽力获取分集列表（GET /v0/episodes?subject_id=…），失败返回 None。

    注意：Bangumi 分集列表的真实路由是 ``/v0/episodes``（以 subject_id
    作查询参数）；``/v0/subjects/{id}/episodes`` 路径在服务端不存在（404）。
    """
    url = f"{BANGUMI_API_BASE}/episodes"
    try:
        data = _get_json(
            url, params={"subject_id": subject_id, "limit": 200, "offset": 0}
        )
    except BangumiHTTPError as exc:
        if exc.status in (400, 404):
            try:
                data = _get_json(url, params={"subject_id": subject_id})
            except (BangumiHTTPError, BangumiNetworkError):
                return None
        else:
            return None
    except BangumiNetworkError:
        return None
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data
    return None


def _fetch_characters(subject_id: int) -> Optional[List[Dict[str, Any]]]:
    """获取角色（含声优）列表（GET /v0/subjects/{id}/characters），失败返回 None。

    该接口直接返回数组；每个角色条目含 ``name`` 与 ``actors``（声优数组）。
    """
    url = f"{BANGUMI_API_BASE}/subjects/{subject_id}/characters"
    try:
        data = _get_json(url)
    except (BangumiHTTPError, BangumiNetworkError):
        return None
    if isinstance(data, list):
        return data
    return None


def parse_character_casts(
    characters: Optional[List[Dict[str, Any]]], limit: int = 15
) -> List[StaffItem]:
    """把角色列表解析为“角色 -> 声优”的 StaffItem 列表（用于详情声优展示）。

    Args:
        characters: ``/v0/subjects/{id}/characters`` 返回的数组。
        limit: 最多取多少个角色（角色多时只展示主要角色）。

    Returns:
        声优条目列表；无数据时为空列表。
    """
    casts: List[StaffItem] = []
    if not isinstance(characters, list):
        return casts
    for character in characters:
        if not isinstance(character, dict):
            continue
        role = clean_text(character.get("name"))
        # Bangumi 角色名常为 “日文名 / 中文名”，取中文部分展示
        if " / " in role:
            role = role.split(" / ")[-1].strip()
        actors = character.get("actors")
        if not isinstance(actors, list):
            continue
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            name = clean_text(actor.get("name"))
            if name:
                casts.append(StaffItem(role=role or "角色", name=name))
                break  # 同名角色可能有多位声优（不同季），仅取第一位
        if len(casts) >= limit:
            break
    return casts


def _main_episode_entries(raw_list: Any) -> List[Dict[str, Any]]:
    """只保留正片分集（type=0 或未标注 type），过滤 SP/OP/ED/MAD 等。

    Bangumi 会把“第0话/特别篇”等作为 type=1 的分集返回，若不过滤会导致
    详情分集列表多出多余集数（如 13 集作品多出“第14集/第0话”）。
    """
    result: List[Dict[str, Any]] = []
    if not isinstance(raw_list, list):
        return result
    for ep in raw_list:
        if not isinstance(ep, dict):
            continue
        ep_type = to_int(ep.get("type"))
        if ep_type not in (None, 0):
            continue
        result.append(ep)
    return result


def parse_season_view(
    data: Any, episodes_data: Optional[Any] = None
) -> SeasonDetail:
    """把 Bangumi Subject 详情（+ 分集响应）解析为 SeasonDetail。

    Args:
        data: ``/v0/subjects/{id}`` 返回的 Subject 字典。
        episodes_data: ``/v0/episodes?subject_id=…`` 的响应（可省略）。

    Returns:
        SeasonDetail 对象。
    """
    detail = _parse_subject_basic(data)
    if detail.episodes:
        # Subject 自带 eps 数组时不再覆盖
        return detail
    if isinstance(episodes_data, dict) and isinstance(
        episodes_data.get("data"), list
    ):
        detail.episodes = [
            EpisodeInfo.from_dict(ep)
            for ep in _main_episode_entries(episodes_data["data"])
        ]
        if detail.episode_total is None and detail.episodes:
            detail.episode_total = len(detail.episodes)
    return detail


async def get_season_detail(subject_id: int) -> SeasonDetail:
    """获取条目完整详情（Bangumi /v0/subjects/{id} + episodes）。

    Args:
        subject_id: Bangumi 条目 ID（界面沿用 “season_id” 的称呼）。

    Returns:
        SeasonDetail 对象。

    Raises:
        BangumiQueryError / BangumiNetworkError / BangumiHTTPError。
    """
    subject_id = to_int(subject_id)
    if not subject_id:
        raise BangumiQueryError("条目 ID 不合法")
    data = _get_json(f"{BANGUMI_API_BASE}/subjects/{subject_id}")
    if not isinstance(data, dict) or to_int(_dig(data, "id")) is None:
        raise BangumiQueryError("未找到该条目，请确认 ID 是否正确")
    episodes = _fetch_episodes(subject_id)  # 失败不影响主体信息
    detail = parse_season_view(data, episodes_data=episodes)
    # 动画条目补充“主要声优”（角色 -> 声优）；失败不影响详情其它信息
    if to_int(_dig(data, "type")) == 2:
        characters = _fetch_characters(subject_id)
        casts = parse_character_casts(characters)
        if casts:
            detail.casts = casts
    if not detail.season_id and not detail.title:
        raise BangumiQueryError("未找到该条目，请确认 ID 是否正确")
    return detail


# ---------------------------------------------------------------------------
# 3. 排行榜
# ---------------------------------------------------------------------------


# “日漫 / 国漫”需要的地区标签（条目地区信息位于 tags/meta_tags）
_JP_REGIONS = ("日本",)
_CN_REGIONS = ("中国", "中国大陆", "中国香港", "中国澳门", "中国台湾", "香港", "澳门", "台湾")

# 排序维度 -> 指标列含义（heat_kind 会显示为表头；评分维度在界面上复用评分列）
_RANK_SORT_KIND = {"热度": "热度", "评分": "评分", "收藏数": "收藏数"}


def _rank_type_no(category: Dict[str, Any]) -> int:
    """类别 -> Bangumi subject type：纪录片/电影=6，其余动画=2。"""
    short = str(category.get("short", ""))
    if short in ("纪录片", "电影"):
        return 6
    return 2


def _region_target(category: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
    """返回该类别要求条目地区命中的标签集合；不区分地区返回 None。"""
    short = str(category.get("short", ""))
    if short == "日漫":
        return _JP_REGIONS
    if short == "国漫":
        return _CN_REGIONS
    return None


def _rank_metric(raw: Dict[str, Any], sort_key: str) -> float:
    """按排序维度取条目指标数值（用于客户端排序）。

    - 热度   ：rating.total（评分人数，人气代理）
    - 评分   ：rating.score
    - 收藏数 ：collection 各状态总数
    """
    if sort_key == "评分":
        value = to_float(_dig(raw, "rating.score"))
        return value if value is not None else 0.0
    if sort_key == "收藏数":
        total = subject_collection_total(raw)
        return float(total) if total is not None else 0.0
    total = to_int(_dig(raw, "rating.total"))
    return float(total) if total is not None else 0.0


def _rank_pool(category: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从可翻页列表接口 GET /v0/subjects?type=&sort=rank 拉取候选池。

    说明：POST /v0/search/subjects 的空关键词榜单每请求只返回固定前 10
    且分页无效；GET /v0/subjects 支持 ``sort=rank`` 与真正的 offset 分页，
    因此用它对榜单做“先取候选池、再按维度本地排序”的实现。
    """
    type_no = _rank_type_no(category)
    url = f"{BANGUMI_API_BASE}/subjects"
    want = max(1, int(config.RANK_POOL_SIZE))
    limit = 25
    offset = 0
    pool: List[Dict[str, Any]] = []
    while len(pool) < want:
        try:
            data = _get_json(
                url,
                params={"type": type_no, "sort": "rank",
                        "limit": limit, "offset": offset},
            )
        except (BangumiHTTPError, BangumiNetworkError):
            break
        if not isinstance(data, dict):
            break
        items = data.get("data")
        if not isinstance(items, list) or not items:
            break
        pool.extend(item for item in items if isinstance(item, dict))
        offset += len(items)
    return pool[:want]


def _region_kept(raw: List[Dict[str, Any]],
                 category: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按地区标签过滤“日漫 / 国漫”候选池。"""
    wanted = _region_target(category)
    if not wanted:
        return raw
    return [
        item for item in raw
        if any(area in wanted for area in subject_areas(item))
    ]


async def fetch_ranking(
    category: Dict[str, Any], sort_key: str = "热度"
) -> List[RankItem]:
    """获取某类别（config.RANK_CATEGORIES 中的一项）的排行榜。

    流程：GET /v0/subjects 拉取候选池（默认 150 条）-> 日漫/国漫按地区
    标签过滤 -> 按所选的排序维度（热度/评分/收藏数）降序 -> 取前 50。

    Args:
        category: config.RANK_CATEGORIES 中的一项。
        sort_key: 排序维度，取 config.RANK_SORT_KEYS 之一（默认“热度”）。

    Returns:
        带名次（1..N，N<=config.RANK_LIMIT=50）的 RankItem 列表。

    Raises:
        BangumiQueryError: 榜单为空或地区过滤后无匹配条目时。
    """
    if sort_key not in _RANK_SORT_KIND:
        sort_key = "热度"
    raw = _region_kept(_rank_pool(category), category)
    if not raw:
        short = str(category.get("short", ""))
        if _region_target(category) is not None:
            raise BangumiQueryError(
                f"「{short}」排行榜候选池中暂无该地区条目，"
                "可尝试选择「全部」查看"
            )
        raise BangumiQueryError(f"「{short}」排行榜暂无数据，请稍后重试")

    # 按维度降序（稳定：指标相同保持服务端顺序）
    raw.sort(key=lambda item: _rank_metric(item, sort_key), reverse=True)
    picked = raw[: config.RANK_LIMIT]

    kind = _RANK_SORT_KIND.get(sort_key, "热度")
    items: List[RankItem] = []
    for idx, item in enumerate(picked, start=1):
        rank_item = RankItem.from_dict(item, rank=idx, heat_kind_default=kind)
        metric = _rank_metric(item, sort_key)
        if metric > 0:
            rank_item.heat_value = metric
        rank_item.heat_kind = kind
        items.append(rank_item)
    return items


def parse_rank_list(raw_items: Sequence[Dict[str, Any]]) -> List[RankItem]:
    """把 Bangumi Subject 原始条目解析为带名次的 RankItem 列表。

    供测试与旧接口使用；真实榜单流程见 ``fetch_ranking``。

    Args:
        raw_items: Subject 原始字典列表。

    Returns:
        已排名（rank 从 1 开始）的 RankItem 列表。
    """
    parsed: List[RankItem] = [
        RankItem.from_dict(item, rank=idx)
        for idx, item in enumerate(raw_items, start=1)
        if isinstance(item, dict)
    ]
    return parsed


# ---------------------------------------------------------------------------
# 4. 追番日历（每周放送表）
# ---------------------------------------------------------------------------


_WEEKDAY_CN_MAP: Dict[str, int] = {
    name: idx for idx, name in enumerate(config.WEEKDAY_NAMES, start=1)
}


def _weekday_number(weekday: Any) -> Optional[int]:
    """把 Bangumi 星期对象换算为 1=周一 … 7=周日。"""
    if isinstance(weekday, dict):
        day_id = to_int(weekday.get("id"))
        if day_id is not None:
            if 1 <= day_id <= 7:
                return day_id
            if day_id == 0:  # Bangumi：0 表示周日
                return 7
        cn = str(weekday.get("cn") or "")
        if cn in _WEEKDAY_CN_MAP:
            return _WEEKDAY_CN_MAP[cn]
    return None


def parse_timeline(data: Any) -> List[TimelineDay]:
    """把 Bangumi 日历（/calendar）响应解析为按星期排序的 TimelineDay 列表。

    响应形态（数组）示例：:

        [
          {"weekday": {"id": 1, "cn": "周一", ...}, "items": [Subject...]},
          {"weekday": {"id": 2, "cn": "周二", ...}, "items": [Subject...]},
          ...
        ]

    weekday.id 语义：1=周一 … 7=周日（若个别实现用 0 表示周日亦兼容）。

    Args:
        data: ``/calendar`` 返回的 JSON。

    Returns:
        按 1=周一 … 7=周日 排序的 TimelineDay 列表（只保留有数据的星期）。
    """
    if not isinstance(data, list):
        return []
    days: List[TimelineDay] = []
    for group in data:
        if not isinstance(group, dict):
            continue
        weekday = _weekday_number(group.get("weekday"))
        if weekday is None:
            continue
        raw_items = group.get("items") or group.get("data")
        if not isinstance(raw_items, list):
            continue
        items: List[TimelineItem] = []
        for raw in raw_items:
            if isinstance(raw, dict):
                item = TimelineItem.from_dict(raw)
                if item.season_id is not None or item.title:
                    items.append(item)
        if not items:
            continue
        days.append(
            TimelineDay(
                date="",
                weekday=weekday,
                weekday_cn=config.WEEKDAY_NAMES[weekday - 1],
                is_today=False,  # Bangumi 日历无“今天”语义
                items=items,
            )
        )
    days.sort(key=lambda d: d.weekday)
    return days


def _resolve_airing_episode(
    episodes_data: Optional[Dict[str, Any]], target_date: str
) -> Optional[int]:
    """在正片分集中解析某放送日期对应的话数（用于追番日历“更新集数”）。

    优先按 airdate 精确匹配目标日期；匹配不到时退化为“不晚于该日已播出
    的最新话数”（如未来话尚未登记时给出已更新到的集数）。

    Args:
        episodes_data: ``/v0/episodes?subject_id=…`` 的响应（可为 None）。
        target_date: 形如 ``YYYY-MM-DD`` 的放送日期。

    Returns:
        话数（从 1 开始）；无法确定时返回 None。
    """
    if not isinstance(episodes_data, dict):
        return None
    main = _main_episode_entries(episodes_data.get("data"))
    target = str(target_date or "")[:10]
    if not main or not target:
        return None
    best: Optional[int] = None
    for ep in main:
        air = str(
            ep.get("airdate") or ep.get("air_date") or ep.get("pub_time") or ""
        )[:10]
        number = to_int(ep.get("ep") or ep.get("sort"))
        if number is None:
            continue
        if air and air == target:
            return number
        if air and air <= target:
            if best is None or number > best:
                best = number
    return best


async def fetch_airing_episode_number(
    subject_id: int, air_date: str
) -> Optional[int]:
    """查询某条目在给定放送日期的话数（保留接口）。

    Args:
        subject_id: Bangumi 条目 ID。
        air_date: 形如 ``YYYY-MM-DD`` 的放送日期。

    Returns:
        话数；分集接口失败或无法确定时返回 None。
    """
    episodes = _fetch_episodes(subject_id)
    return _resolve_airing_episode(episodes, air_date)


def _resolve_first_episode_date(
    episodes_data: Optional[Dict[str, Any]],
) -> Optional[str]:
    """从正片分集里取最早一话的播出日期（作为“开播时间/首话日期”）。"""
    if not isinstance(episodes_data, dict):
        return None
    dates: List[str] = []
    for ep in _main_episode_entries(episodes_data.get("data")):
        air = str(
            ep.get("airdate") or ep.get("air_date") or ep.get("pub_time") or ""
        ).strip()[:10]
        if len(air) == 10:
            dates.append(air)
    return min(dates) if dates else None


async def fetch_first_episode_date(subject_id: int) -> Optional[str]:
    """查询条目的首话播出日期（追番日历“开播时间”列用）。

    Args:
        subject_id: Bangumi 条目 ID。

    Returns:
        形如 ``YYYY-MM-DD`` 的首话日期；无法获取时返回 None。
    """
    episodes = _fetch_episodes(subject_id)
    return _resolve_first_episode_date(episodes)


async def fetch_timeline(
    type_value: int = config.SEASON_TYPE_ANIME,
) -> List[TimelineDay]:
    """获取每周放送表（Bangumi 根路径 /calendar，非 /v0 下）。

    Bangumi 日历仅提供“动画每周更新日”维度的数据（放送中动画），
    无法区分日漫 / 国漫 / 影视；``type_value`` 参数保留用于菜单兼容，
    本实现不会对日历结果做类型强过滤（尽力过滤失败时返回全部）。

    Args:
        type_value: 菜单类型值（1 番剧 / 3 影视 / 4 国创），仅用于兼容。

    Returns:
        按星期排序的 TimelineDay 列表。

    Raises:
        BangumiNetworkError / BangumiHTTPError: 网络或接口错误。
    """
    allowed_types: Dict[int, List[int]] = {
        config.SEASON_TYPE_ANIME: [2],
        config.SEASON_TYPE_GUOCHUANG: [2],
        3: [6],
    }
    wanted = allowed_types.get(int(type_value), [2])
    data = _get_json(config.BANGUMI_CALENDAR_URL)
    days = parse_timeline(data)
    if not days:
        return days
    # 尽力过滤：筛不出结果（例如无三次元放送表）时退回全部
    filtered = [
        TimelineDay(
            date=day.date,
            weekday=day.weekday,
            weekday_cn=day.weekday_cn,
            is_today=day.is_today,
            items=[item for item in day.items if item.category in (
                bangumi_type_name(t) for t in wanted
            )],
        )
        for day in days
    ]
    filtered = [day for day in filtered if day.items]
    return filtered or days
