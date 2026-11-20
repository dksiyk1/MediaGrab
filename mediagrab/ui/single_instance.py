"""单实例守卫：第二次启动不开新窗口，而是把已有窗口唤到前面。

为什么需要：两个实例会同时往同一个 history.db 写、同时下同一个文件。
下载层的路径锁是进程内的，管不到另一个进程，最后就是互相覆盖。

实现用 QLocalServer 的命名管道（Windows 下是 \\\\.\\pipe\\...），
不用文件锁 —— 进程被杀后残留的管道能被 removeServer 清掉，而文件锁不会。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

KEY = "MediaGrab.SingleInstance.v1"


def guard(app, on_activate: Callable[[], None]) -> bool:
    """True = 本进程可以继续启动；False = 已有实例，已通知它激活，本次应退出。"""
    probe = QLocalSocket()
    probe.connectToServer(KEY)
    if probe.waitForConnected(400):
        try:
            probe.write(b"show")
            probe.flush()
            probe.waitForBytesWritten(400)
        except Exception:  # noqa: BLE001
            pass
        finally:
            probe.disconnectFromServer()
        return False

    # 上次异常退出可能留下同名管道，不清掉 listen 会一直失败
    QLocalServer.removeServer(KEY)
    server = QLocalServer(app)
    if not server.listen(KEY):
        return True          # 拿不到锁也不该拦住用户

    server.newConnection.connect(lambda: _on_connect(server, on_activate))
    app._mediagrab_single_server = server   # 持有引用，别被 GC 回收
    return True


def _on_connect(server: QLocalServer, on_activate: Callable[[], None]) -> None:
    while server.hasPendingConnections():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
            conn.close()
    try:
        on_activate()
    except Exception:  # noqa: BLE001
        pass
