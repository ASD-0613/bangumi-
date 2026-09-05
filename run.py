"""启动脚本：python run.py —— 启动图形界面（PyQt5）。

等价于 ``python -m bilibili_bangumi.main``。
命令行版（备份）入口：``python -m bilibili_bangumi.cli_main``。
"""

from bilibili_bangumi.main import main

if __name__ == "__main__":
    main()
