"""构建 Windows 单文件可执行程序（无控制台窗口）。

用法（项目根目录）：
    python build_exe.py

产物：dist/BangumiQuery-<版本号>.exe

- ``--onefile``：单个 exe 便于分发；首次启动需自解压，稍慢属正常现象；
- ``--windowed``：GUI 程序运行时不弹出黑色控制台窗口；
- 版本号自动取自 ``bangumi_query/config.py``（应用名/版本号唯一来源），
  exe 名称与窗口标题保持一致；
- 构建依赖：``pip install pyinstaller``（仅构建时需要；运行 exe 的用户
  无需安装 Python 与任何依赖）；
- 生成到 build/ 的临时文件与 dist/ 产物均已被 .gitignore 排除，不会混入
  版本管理；exe 本体建议作为 GitHub Release 附件分发。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 排除与本程序无关的重量级依赖：
# - PySide6/PySide2：另一套 Qt 绑定（Anaconda 预装），PyInstaller 禁止两套 Qt 并存；
# - matplotlib/numpy/pandas/zmq/tkinter 等：Anaconda 预装的科学计算栈，GUI 用不到；
# - PyQt5 的 WebEngine/Quick/Network 等子模块：本程序只用 QtCore/QtGui/QtWidgets，
#   联网走 requests。
# 运行时安全性已验证：导入 bangumi_query.main 后 sys.modules 不含以下任何模块。
EXCLUDES: list[str] = [
    "PySide6", "PySide2", "shiboken6", "qtpy",
    "matplotlib", "numpy", "pandas", "scipy", "zmq",
    "tkinter", "_tkinter",
    "IPython", "jedi",
    "PyQt5.QtWebEngine", "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebChannel",
    "PyQt5.QtQuick", "PyQt5.QtQml", "PyQt5.QtMultimedia", "PyQt5.QtSql",
    "PyQt5.QtNetwork",
]


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from bangumi_query import config

    name = f"BangumiQuery-{config.VERSION}"
    exe_path = ROOT / "dist" / f"{name}.exe"

    # v2.9.0 起不再有 run.py：构建时生成一个临时入口脚本（位于已被
    # .gitignore 排除的 build/ 目录），仅负责调用 GUI 主函数。
    entry_dir = ROOT / "build"
    entry_dir.mkdir(exist_ok=True)
    entry = entry_dir / "_entry_gui.py"
    entry.write_text(
        "import sys\n"
        "from bangumi_query.main import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", name,
        "--icon", str(ROOT / "icon.ico"),
        # 图标同时打进包内，供运行时设置窗口/任务栏图标使用
        "--add-data", f"{ROOT / 'icon.ico'};.",
        "--add-data", f"{ROOT / 'bangumi_query/resources'};resources",
        # 生成的 .spec 归入 build/（已被 .gitignore 排除），构建参数以本脚本为准
        "--specpath", "build",
        # 临时入口位于 build/ 子目录，需显式提供项目根目录以定位包
        "--paths", str(ROOT),
        str(entry.relative_to(ROOT)),
    ]
    for module in EXCLUDES:
        cmd.extend(["--exclude-module", module])
    print("[构建]", " ".join(cmd), flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[失败] PyInstaller 返回码 {result.returncode}", file=sys.stderr)
        return result.returncode
    if not exe_path.is_file():
        print(f"[失败] 未找到产物：{exe_path}", file=sys.stderr)
        return 1

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print()
    print(f"[完成] {exe_path}（{size_mb:.1f} MB）")
    print("提示：")
    print("- 首次运行如出现 SmartScreen 警告：点「更多信息」→「仍要运行」"
          "（未签名 exe 的正常现象）")
    print("- 封面缓存仍在 %LOCALAPPDATA%\\BangumiQuery\\cache，与 exe 所在位置无关")
    return 0


if __name__ == "__main__":
    sys.exit(main())
