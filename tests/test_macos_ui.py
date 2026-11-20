"""macOS 版 UI 功能验证：筛选、搜索、全选、卡片接口。

不联网，用构造的 MediaInfo 驱动真实控件。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.models import FormatOption, MediaInfo, MediaItem
from mediagrab.ui.main_window import MainWindow

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def gallery() -> MediaInfo:
    items = [MediaItem(index=i + 1, url=f"a://{i}") for i in range(5)]
    return MediaInfo(
        platform="douyin", title="僵尸初音 唱歌🎶", author="宗圆kzy.",
        items=items, media_type="image",
    )


def video3() -> MediaInfo:
    return MediaInfo(
        platform="douyin", title="我这样的人 #少女感", author="y28.", duration=11,
        formats=[FormatOption("a", "1080P"), FormatOption("b", "720P"), FormatOption("c", "576P")],
    )


def video6() -> MediaInfo:
    return MediaInfo(
        platform="bilibili", title="反诈博主微剑", author="极客湾", duration=3434,
        formats=[FormatOption(str(i), lbl) for i, lbl in
                 enumerate(["8K", "4K", "1080P60", "1080P+", "1080P", "720P"])],
    )


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()

    print("1) 卡片构建")
    for info in (gallery(), video3(), video6()):
        w._add_card(info)
    app.processEvents()
    check("3 张卡片", len(w._cards) == 3, f"got {len(w._cards)}")

    print("2) 侧栏计数")
    counts = {"all": len(w._cards), "image": 0, "video": 0, "done": 0}
    for c in w._cards:
        if c.info.items:
            counts["image"] += 1
        elif c.info.formats:
            counts["video"] += 1
    check("all=3 image=1 video=2", counts["all"] == 3 and counts["image"] == 1 and counts["video"] == 2)

    print("3) 筛选")
    w._on_filter("image")
    app.processEvents()
    vis = [c.isVisible() for c in w._cards]
    check("图片视图只剩图集卡", vis[0] and not vis[1] and not vis[2], str(vis))

    w._on_filter("video")
    app.processEvents()
    vis = [c.isVisible() for c in w._cards]
    check("视频视图只剩两张视频卡", not vis[0] and vis[1] and vis[2], str(vis))

    w._on_filter("done")
    app.processEvents()
    check("已完成视图为空", not any(c.isVisible() for c in w._cards))

    w._on_filter("all")
    app.processEvents()
    check("回到全部，三张都可见", all(c.isVisible() for c in w._cards))

    print("4) 搜索")
    w._on_search("僵尸")
    app.processEvents()
    vis = [c.isVisible() for c in w._cards]
    check("搜「僵尸」命中图集卡", vis[0] and not vis[1] and not vis[2], str(vis))
    w._on_search("极客湾")
    app.processEvents()
    vis = [c.isVisible() for c in w._cards]
    check("按作者搜「极客湾」命中 B站卡", not vis[0] and not vis[1] and vis[2], str(vis))
    w._on_search("")
    app.processEvents()
    check("清空搜索恢复全部", all(c.isVisible() for c in w._cards))

    print("5) 图集全选/取消")
    card = w._cards[0]
    check("默认全选 5/5", len(card.selected_items()) == 5)
    card._toggle_all()
    check("点全选后变 0/5", len(card.selected_items()) == 0)
    card._toggle_all()
    check("再点一次回到 5/5", len(card.selected_items()) == 5)
    card._thumbs[0].set_checked(False)
    card.refresh_count()
    check("取消单张后 4/5", len(card.selected_items()) == 4)

    print("6) 清晰度选择")
    v3 = w._cards[1]
    v6 = w._cards[2]
    check("3 档用分段控件", getattr(v3.format_box, "_combo", None) is None)
    check("6 档降级为下拉框", getattr(v6.format_box, "_combo", None) is not None)
    f = v3.selected_format()
    check("默认选中第一档 1080P", f is not None and f.label == "1080P", str(getattr(f, "label", None)))

    print("7) 下载任务生成")
    tasks = w._build_tasks(card)
    check("图集生成 4 条任务", len(tasks) == 4, f"got {len(tasks)}")
    t2 = w._build_tasks(v3)
    check("视频生成 1 条任务", len(t2) == 1, f"got {len(t2)}")

    print("8) 缩略图地址")
    urls = card.thumbnail_urls()
    check("图集取下全部 5 张地址", len(urls) == 5, f"got {len(urls)}")

    w.close()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
