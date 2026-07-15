"""PyInstaller 运行时钩子 - 在主脚本执行前设置路径"""
import sys
import os

if getattr(sys, 'frozen', False):
    _cli_root = os.path.join(sys._MEIPASS, 'CLI_lite')
    if _cli_root not in sys.path:
        sys.path.insert(0, _cli_root)
