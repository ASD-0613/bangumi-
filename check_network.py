"""网络诊断脚本（独立排障工具，不属于程序功能）。

用法（项目根目录）：
    python check_network.py

依次检测并打印：
1. 相关环境变量与系统代理（requests 会读取 Windows 系统代理）
2. DNS 解析 api.bgm.tv
3. TCP 443 连通性（IPv4 / IPv6 分别尝试，仅供直连参考）
4. HTTPS/TLS + HTTP 状态（与程序同路径的真实请求，结论以此为准）
5. 简要结论与建议

说明：
- 本机若配置了系统代理，requests 会自动使用（无需设置 BANGUMI_PROXY），
  此时“裸 TCP 直连失败”不代表程序不可用，最终以第 4 步为准；
- 不改动任何界面 / 操作逻辑 / 功能；本脚本仅用于确认“网络是否可用”。
"""

from __future__ import annotations

import os
import socket
import sys
import time
import urllib.request
from typing import List, Tuple

import requests

from bangumi_query import config

_HOST: str = "api.bgm.tv"
_PORT: int = 443
_PING_URL: str = "https://api.bgm.tv/ping"

# 每个地址的 TCP 连接超时（秒），避免被某个不可达的 IP（如 IPv6）拖垮
_TCP_ADDR_TIMEOUT: float = 4.0


def _system_proxies() -> List[str]:
    """返回 requests 可能使用的系统代理（含 Windows 注册表代理）。"""
    detected: List[str] = []
    try:
        proxies = urllib.request.getproxies()
        for key in ("https", "http", "all"):
            value = proxies.get(key) or proxies.get(key.upper())
            if value:
                label = f"{key}=" if key != "all" else ""
                detected.append(f"{label}{value}")
    except Exception:  # noqa: BLE001 - 诊断脚本不因异常中断
        pass
    return detected


def _env_block() -> List[str]:
    """读取与本程序网络相关的环境变量与系统代理。"""
    lines: List[str] = ["[1] 环境变量 / 代理"]
    keys = (
        "BANGUMI_API_BASE",
        "BANGUMI_PROXY",
        "BANGUMI_CONNECT_TIMEOUT",
        "BANGUMI_READ_TIMEOUT",
        "BANGUMI_RETRY_TIMES",
        "BANGUMI_INSECURE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    )
    for key in keys:
        value = os.environ.get(key, "")
        lines.append(f"    {key} = {value if value else '（未设置）'}")
    system_proxies = _system_proxies()
    if system_proxies:
        lines.append("    系统代理（requests 可能自动使用）:")
        for proxy in system_proxies:
            lines.append(f"        {proxy}")
    else:
        lines.append("    系统代理 = （未检测到）")
    lines.append(f"    当前 API 基址 = {config.BANGUMI_API_BASE}")
    lines.append(f"    当前 User-Agent = BangumiQuery/{config.VERSION}")
    return lines


def _dns_check() -> Tuple[bool, List[str]]:
    """DNS 解析检查。"""
    lines: List[str] = ["[2] DNS 解析"]
    try:
        infos = socket.getaddrinfo(_HOST, _PORT, type=socket.SOCK_STREAM)
        addresses = sorted({info[4][0] for info in infos})
        lines.append(
            f"    解析成功，共 {len(addresses)} 个 IP：{', '.join(addresses[:4])}"
        )
        return True, lines
    except socket.gaierror as exc:
        lines.append(f"    解析失败：{exc}")
        return False, lines


def _tcp_check() -> Tuple[bool, List[str]]:
    """TCP 443 直连检查（IPv4 优先、IPv6 其次，仅供参考）。"""
    lines: List[str] = ["[3] TCP 443 直连（不经代理，仅供参考）"]
    try:
        infos = socket.getaddrinfo(_HOST, _PORT, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        lines.append(f"    解析失败：{exc}")
        return False, lines

    # 去重并先尝试 IPv4，避免 IPv6 黑洞耗尽整体超时；
    # getaddrinfo 的 IPv6 地址为 4 元组，需归一化为 (host, port)
    seen: set = set()
    ordered: List[Tuple[str, Tuple[str, int]]] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        for info in infos:
            key = info[4][0]
            if info[0] == family and key not in seen:
                seen.add(key)
                ordered.append((family, (info[4][0], info[4][1])))

    succeeded: bool = False
    for idx, (family, sockaddr) in enumerate(ordered):
        kind = "IPv4" if family == socket.AF_INET else "IPv6"
        start = time.time()
        try:
            with socket.create_connection(sockaddr, timeout=_TCP_ADDR_TIMEOUT):
                elapsed = (time.time() - start) * 1000
                lines.append(
                    f"    [{kind}] {sockaddr[0]} 连接成功（{elapsed:.0f} ms）"
                )
                succeeded = True
                break
        except OSError as exc:
            elapsed = (time.time() - start) * 1000
            lines.append(
                f"    [{kind}] {sockaddr[0]} 连接失败（{elapsed:.0f} ms）："
                f"{type(exc).__name__}"
            )
    if not succeeded:
        lines.append(
            "    （直连失败不一定是问题：requests 可能已自动走系统代理，"
            "最终以第 4 步 HTTPS 结果为准）"
        )
    return succeeded, lines


def _http_check() -> Tuple[bool, List[str]]:
    """HTTPS/TLS + HTTP 状态检查（与程序同路径的真实请求，判定以此为准）。"""
    lines: List[str] = ["[4] HTTPS / HTTP 状态"]
    try:
        resp = requests.get(
            _PING_URL,
            headers={
                "User-Agent": f"BangumiQuery/{config.VERSION}",
                "Accept": "application/json",
            },
            timeout=(config.CONNECT_TIMEOUT, config.REQUEST_TIMEOUT),
            verify=config.SSL_VERIFY,
        )
        lines.append(
            f"    HTTP {resp.status_code}"
            f"（耗时 {(resp.elapsed.total_seconds()) * 1000:.0f} ms）"
        )
        content_type = resp.headers.get("Content-Type", "")
        lines.append(f"    响应头 Content-Type: {content_type}")
        return resp.status_code == 200, lines
    except requests.exceptions.ConnectTimeout:
        lines.append("    [失败] 连接超时：无法建立 TCP 连接（多为网络/防火墙/代理问题）")
        return False, lines
    except requests.exceptions.ReadTimeout:
        lines.append("    [失败] 读取超时：能连接但服务器未及时响应")
        return False, lines
    except requests.exceptions.SSLError as exc:
        lines.append(f"    [失败] SSL/TLS 错误：{exc}")
        lines.append("       若确因证书链问题，可设置 BANGUMI_INSECURE=1 后重试（不推荐）")
        return False, lines
    except requests.exceptions.ProxyError as exc:
        lines.append(f"    [失败] 代理错误：{exc}")
        lines.append("       请检查 BANGUMI_PROXY / 系统代理是否可用")
        return False, lines
    except requests.exceptions.ConnectionError as exc:
        lines.append(f"    [失败] 连接错误：{exc}")
        return False, lines
    except Exception as exc:  # noqa: BLE001 - 诊断脚本兜底
        lines.append(f"    [失败] 未知异常：{type(exc).__name__}: {exc}")
        return False, lines


def _summary(
    dns_ok: bool, tcp_ok: bool, http_ok: bool
) -> str:
    """结论：以 HTTPS/HTTP（与程序同路径）是否成功为准。"""
    if http_ok:
        if tcp_ok:
            return "[通过] 网络链路正常（直连与 requests 路径均可），程序可正常访问 Bangumi API。"
        return (
            "[通过] requests 可正常访问 Bangumi（可能经由系统代理）。"
            "裸 TCP 直连失败不影响程序，可直接运行程序使用。"
        )
    if not dns_ok:
        return "[失败] DNS 解析失败：请检查 DNS 设置，或换一个网络后重试。"
    return (
        "[失败] 无法通过 requests 访问 api.bgm.tv。请检查：本机是否可上网、"
        "代理是否可用（BANGUMI_PROXY / 系统代理）、"
        "防火墙/路由是否放行该域名；可尝试换网络后再运行本脚本。"
    )


def main() -> int:
    """诊断入口，返回 0=可访问（HTTP 通过），1=存在问题。"""
    print("=" * 60)
    print("Bangumi API 网络诊断")
    print("=" * 60)

    ok_dns, dns_lines = _dns_check()
    ok_tcp, tcp_lines = _tcp_check()
    ok_http, http_lines = _http_check()

    for block in (_env_block(), dns_lines, tcp_lines, http_lines):
        for line in block:
            print(line)
        print("-" * 60)

    print(_summary(ok_dns, ok_tcp, ok_http))
    return 0 if ok_http else 1


if __name__ == "__main__":
    sys.exit(main())
