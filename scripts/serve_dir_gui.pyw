# serve_dir_gui.pyw — 双击入口（Windows 把 .pyw 关联到 pythonw.exe，运行即无控制台）。
# 逻辑与 serve_dir_gui.py 完全一致；此文件只是一个薄入口。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from serve_dir_gui import main

main()
