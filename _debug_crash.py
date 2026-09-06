"""临时探针：尝试在无头环境复现悬停 qFatal（用后即删）。

捕获并打印所有 Qt 消息（qFatal 文本会显式出现）；
模拟真实条件：图标加载到达 + 首播日期回填 + 悬停跑马灯。
"""
import faulthandler
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
faulthandler.enable()

from bangumi_query import main as gui
from bangumi_query.models.bangumi import TimelineDay, TimelineItem
from PyQt5.QtCore import QEvent, Qt, qInstallMessageHandler
from PyQt5.QtGui import QIcon, QMouseEvent


def qt_handler(mode, _ctx, message):
    names = {0: "debug", 1: "warning", 2: "critical", 3: "FATAL", 4: "info"}
    print(f"[qt-{names.get(mode, mode)}] {message}", flush=True)


qInstallMessageHandler(qt_handler)

theme = sys.argv[1] if len(sys.argv) > 1 else "浅色"
app = gui.QApplication(sys.argv)
app.setFont(gui.QFont("Microsoft YaHei UI", 10))
app.setStyleSheet(gui.THEMES.get(theme, gui.STYLE_SHEET_LIGHT))
window = gui.MainWindow()
window.show()
window.resize(1100, 760)


def _it(i, title):
    return TimelineItem(season_id=i, title=title, cover="",
                        pub_time="", ep_label="", category="动画")


day = TimelineDay(date="", weekday=1, weekday_cn="周一", is_today=False, items=[
    _it(1, "无职转生 ～到了异世界就拿出真本事～ 第三季 特别版"),
    _it(2, "葬送的芙莉莲 第二季"),
    _it(3, "药屋少女的呢喃"),
    _it(4, "Re:从零开始的异世界生活 第三季"),
    _it(5, "青之箱"),
    _it(6, "我独自升级"),
])
window._timeline_days = [day]
window._timeline_selected = 1
window._render_timeline_day()
for _ in range(30):
    app.processEvents()

# 模拟真实条件：封面图标加载到达 + 首播日期回填
pm = window._placeholder_pixmap()
for i in range(window.timeline_tree.topLevelItemCount()):
    window.timeline_tree.topLevelItem(i).setIcon(0, QIcon(pm))
window.timeline_tree.topLevelItem(0).setText(2, "2026-01-11")

# 悬停第一行（长名称，走跑马灯路径）
row = window.timeline_tree.topLevelItem(0)
rect = window.timeline_tree.visualItemRect(row)
ev = QMouseEvent(QEvent.MouseMove, rect.center(), Qt.NoButton,
                 Qt.NoButton, Qt.NoModifier)
app.sendEvent(window.timeline_tree.viewport(), ev)
print(f"[probe] hover_row={window._name_delegate._hover_row}", flush=True)

for i in range(200):  # ~4 秒跑马灯重绘循环
    app.processEvents()
    time.sleep(0.02)

print("SURVIVED", flush=True)
window.close()
