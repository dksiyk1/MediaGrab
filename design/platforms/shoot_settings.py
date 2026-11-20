"""给设置窗口拍照（含系统标题栏）。

Qt 的 QScreen.grabWindow 只回客户区，用 Win32 PrintWindow 才能连标题栏一起抓。
"""
import sys
import time

sys.path.insert(0, ".")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.config import Config
from mediagrab.ui.styles import QSS
from tools.grab_window import grab_window

app = QApplication(sys.argv)
app.setFont(QFont("Microsoft YaHei UI", 9))
app.setStyleSheet(QSS if isinstance(QSS, str) else QSS.format())

from mediagrab.ui.settings_dialog import SettingsDialog

dlg = SettingsDialog(Config.load())
dlg.show()
dlg.raise_()
dlg.activateWindow()
for _ in range(90):
    app.processEvents()
    time.sleep(0.01)
time.sleep(0.4)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)

print("client", dlg.size(), "frame", dlg.frameGeometry().getRect())
img = grab_window(dlg)
print("grab", img.size())
img.save("_shots/settings_tuned.png")

for i, name in enumerate(["general", "cookie", "network"]):
    dlg.tabs.setCurrentIndex(i)
    for _ in range(15):
        app.processEvents()
        time.sleep(0.01)
    grab_window(dlg).save(f"_shots/settings_tuned_{name}.png")
print("done")
