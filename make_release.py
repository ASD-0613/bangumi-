"""一键发版：打 zip + 创建/覆盖当前版本的 GitHub Release。

发版策略（自 v3.0.0 起）：**一个分支最多一个 Release**；若远端已存在
同名 tag 的 Release，则删除旧的（连同远端 tag）再以当前代码重建——即
“覆盖”语义。

用法（项目根目录，需先构建 exe）：
    python build_exe.py        # 生成 dist/BangumiQuery-<版本号>.exe
    python make_release.py

行为细节：
- 版本号/tag 取自 ``bangumi_query/config.py``（tag = v<版本号>）；
- zip 内容：exe（顶层，解压即可用）+ 完整源码 + 测试 + 文档；
- GitHub 凭据来自本机 Git 凭据管理器（``git credential fill``），
  令牌只在内存使用，不落盘、不打印；
- 附件上传失败自动重试（最多 5 次）。
"""

from __future__ import annotations

import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPO = "ASD-0613/bangumi-"
UPLOAD_ATTEMPTS = 5


def _token() -> str:
    """从 Git 凭据管理器取 github.com 的访问令牌（不打印）。"""
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("[失败] 未能从凭据管理器取得 GitHub 凭据，请先手动 push 一次")


def _build_zip(top: str, exe: Path) -> Path:
    out = ROOT / f"{top}.zip"
    entries = [(exe, f"{top}/{exe.name}")]
    for name in ("README.md", "requirements.txt", "check_network.py",
                 "build_exe.py", "make_release.py", ".gitignore"):
        if (ROOT / name).is_file():
            entries.append((ROOT / name, f"{top}/{name}"))
    for d in ("bangumi_query", "tests"):
        for p in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" not in p.parts:
                entries.append((p, f"{top}/{p.as_posix()}"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in entries:
            zf.write(src, arc)
    with zipfile.ZipFile(out) as zf:
        if zf.testzip() is not None:
            raise SystemExit("[失败] zip 完整性校验未通过")
    print(f"[zip] {out.name}（{out.stat().st_size / 1024 / 1024:.1f} MB，"
          f"{len(entries)} 项）")
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from bangumi_query import config

    version = config.VERSION
    tag = f"v{version}"
    top = f"BangumiQuery-{version}"
    exe = ROOT / "dist" / f"{top}.exe"
    if not exe.is_file():
        raise SystemExit(f"[失败] 未找到 {exe}，请先运行 python build_exe.py")

    headers = {"Authorization": f"Bearer {_token()}",
               "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{REPO}"

    # 1. 删除远端旧 tag 与同名 Release（覆盖策略）
    #    tag 删除必须验证成功：若远端 tag 仍在，新建 Release 会 422
    for attempt in range(3):
        subprocess.run(["git", "push", "origin", f":refs/tags/{tag}"],
                       capture_output=True, text=True)
        check = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", tag],
            capture_output=True, text=True,
        )
        if not check.stdout.strip():
            break
        print(f"[tag] 远端 tag 删除未生效（第 {attempt + 1}/3 次），重试…")
        time.sleep(5)
    else:
        check = subprocess.run(["git", "ls-remote", "--tags", "origin", tag],
                               capture_output=True, text=True)
        if check.stdout.strip():
            raise SystemExit("[失败] 远端 tag 仍未删除（网络不稳定？），"
                             "请恢复网络后重跑本脚本")
    r = requests.get(f"{api}/releases/tags/{tag}", headers=headers,
                     timeout=(30, 120))
    if r.status_code == 200:
        d = requests.delete(f"{api}/releases/{r.json()['id']}",
                            headers=headers, timeout=(30, 120))
        print(f"[release] 已移除旧 Release（HTTP {d.status_code}）")
    elif r.status_code != 404:
        raise SystemExit(f"[失败] 查询旧 Release：HTTP {r.status_code}")

    # 2. 本地建 tag 并指向当前 HEAD
    subprocess.run(["git", "tag", "-f", tag, "HEAD"], check=True,
                   capture_output=True, text=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()

    # 3. 创建 Release
    body = (
        f"Bangumi 番剧数据查询工具 {tag}\n"
        "数据来自 Bangumi 公开 API（https://api.bgm.tv），"
        "以 Windows 单文件 exe 分发：双击即用、无控制台窗口、无需安装 Python。\n\n"
        "## 使用\n\n"
        f"解压后双击 `{top}.exe`。\n\n"
        "- 首次运行如出现 SmartScreen 警告：点「更多信息」→「仍要运行」"
        "（未签名 exe 的正常现象）；\n"
        "- 需能访问 api.bgm.tv（网络不佳可配置代理，见 README「网络与代理配置」）；\n"
        "- 封面缓存在 %LOCALAPPDATA%\\BangumiQuery\\cache，可在程序底部一键清除。\n\n"
        "## 附件\n\n"
        f"- `{top}.zip`：exe + 完整源码 + 测试（解压即可用）\n"
        f"\n本次更新内容详见仓库 README「修订记录」的 {tag} 小节。"
    )
    r = requests.post(
        f"{api}/releases",
        headers=headers,
        json={"tag_name": tag, "target_commitish": sha,
              "name": f"Bangumi 番剧数据查询工具 {tag}", "body": body},
        timeout=(30, 120),
    )
    if r.status_code not in (200, 201):
        raise SystemExit(f"[失败] 创建 Release：HTTP {r.status_code} "
                         f"{r.json().get('message', '')[:200]}")
    release_id, html = r.json()["id"], r.json()["html_url"]
    print(f"[release] 已创建：{html}")
    # 删除本地 tag：避免与同名分支产生 push 引用歧义
    # （远端 tag 已由 GitHub 依据本 Release 自动建立）
    subprocess.run(["git", "tag", "-d", tag], capture_output=True, text=True)

    # 4. 上传附件（失败重试）
    zip_path = _build_zip(top, exe)
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        print(f"[上传] 第 {attempt}/{UPLOAD_ATTEMPTS} 次尝试…", flush=True)
        try:
            with open(zip_path, "rb") as f:
                up = requests.post(
                    f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets",
                    params={"name": zip_path.name},
                    headers={**headers, "Content-Type": "application/zip"},
                    data=f, timeout=(30, 900),
                )
            if up.status_code in (200, 201):
                print(f"[完成] {up.json()['browser_download_url']}")
                print(f"Release 就绪：{html}")
                return 0
            print(f"  HTTP {up.status_code}: {up.json().get('message', '')[:200]}")
        except requests.RequestException as exc:
            print(f"  网络异常: {type(exc).__name__}")
        time.sleep(8)
    print("[失败] 附件上传多次失败——Release 已存在，可稍后手动补传附件")
    return 1


if __name__ == "__main__":
    sys.exit(main())
