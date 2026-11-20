"""下载历史存储层测试：批次、状态聚合、重试重置、任务重建。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediagrab.core.history import HistoryStore, human_size  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def entry(i: int, **kw) -> dict:
    base = dict(
        source_url="https://v.douyin.com/dcJJwUpngMI/",
        platform="douyin",
        title="就算变成小僵尸，也还是想唱歌🎶",
        author="宗圆kzy.",
        media_type="image",
        kind="gallery",
        format_label="3 张图片",
        url=f"https://p3-pic.douyinpic.com/a_{i}~tplv.webp?x-sign=abc{i}",
        output_dir=r"C:\Users\me\Downloads\MediaGrab",
        filename=f"僵尸初音_{i:02d}",
        ext="jpg",
        referer="https://www.douyin.com/",
        headers={"Cookie": "sessionid=xyz", "User-Agent": "UA/1.0"},
    )
    base.update(kw)
    return base


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mg_hist_")
    db = os.path.join(tmp, "history.db")
    store = HistoryStore(db)

    print("1) 新建批次")
    entries = [entry(i) for i in range(1, 4)]
    batch = store.begin_batch(entries)
    ids = store.batch_ids_after_insert(batch)
    check("返回 batch_id", bool(batch) and len(batch) == 12, batch)
    check("三行记录", len(ids) == 3, str(ids))
    recs = store.records_of(batch)
    check("初始状态全是 pending", all(r.status == "pending" for r in recs))
    check("headers 反序列化成 dict", recs[0].headers.get("Cookie") == "sessionid=xyz",
          str(recs[0].headers))

    print("2) 标记结果")
    store.mark(ids[0], True, r"C:\out\a_01.jpg", 233_000, "", "internal")
    store.mark(ids[1], True, r"C:\out\a_02.jpg", 210_000, "", "internal")
    store.mark(ids[2], False, "", 0, "HTTP 403 Forbidden", "internal")
    recs = store.records_of(batch)
    check("两成两败", sum(r.ok for r in recs) == 2 and sum(r.failed for r in recs) == 1)
    check("失败原因落库", recs[2].message == "HTTP 403 Forbidden", recs[2].message)

    print("3) 批次聚合")
    b = store.batches()[0]
    check("total=3", b.total == 3, str(b.total))
    check("ok=2 failed=1", (b.ok, b.failed) == (2, 1), f"{b.ok}/{b.failed}")
    check("大小累加", b.size == 443_000, str(b.size))
    check("状态=partial", b.status == "partial", b.status)
    check("有 sample_path", bool(b.sample_path), b.sample_path)
    check("标题正确", b.title.startswith("就算变成小僵尸"), b.title)

    print("4) 任务重建（重试的前提）")
    failed = store.failed_records(batch)
    check("只捞出失败项", len(failed) == 1 and failed[0].id == ids[2])
    kw = failed[0].to_task_kwargs()
    from mediagrab.core.downloaders.base import DownloadTask

    task = DownloadTask(**kw)
    check("url 还原", task.url.endswith("a_3~tplv.webp?x-sign=abc3"), task.url)
    check("Cookie 还原", task.headers.get("Cookie") == "sessionid=xyz", str(task.headers))
    check("referer 还原", task.referer == "https://www.douyin.com/", task.referer)
    check("输出目录还原", task.output_dir == r"C:\Users\me\Downloads\MediaGrab", task.output_dir)
    check("文件名与扩展名还原", (task.filename, task.ext) == ("僵尸初音_03", "jpg"))

    print("5) 重试 = 原地重置")
    store.reset_for_retry([ids[2]])
    r = store.records_of(batch)[2]
    check("状态回到 pending", r.status == "pending", r.status)
    check("attempts +1", r.attempts == 2, str(r.attempts))
    check("旧错误清空", r.message == "", r.message)
    check("已成功的行不受影响", store.records_of(batch)[0].status == "ok")
    check("重试后不再是失败项", len(store.failed_records(batch)) == 0)
    b = store.batches()[0]
    check("批次状态回到 pending", b.status == "pending", b.status)

    print("6) 视频任务的合并字段")
    vid = store.begin_batch([entry(9, kind="video", media_type="video", ext="mp4",
                                   format_selector="30080+30280", merge=True,
                                   page_url="https://www.bilibili.com/video/BV1pmx")])
    r = store.records_of(vid)[0]
    kw = r.to_task_kwargs()
    check("merge 保留", kw["merge"] is True, str(kw["merge"]))
    check("format_selector 保留", kw["format_selector"] == "30080+30280")
    check("page_url 保留", kw["page_url"].endswith("BV1pmx"))

    print("7) 搜索")
    check("按标题搜到", len(store.batches(query="僵尸")) == 2, str(len(store.batches(query="僵尸"))))
    check("搜不到返回空", len(store.batches(query="不存在的关键词")) == 0)

    print("8) 统计与清理")
    st = store.stats()
    check("统计批次=2", st["batches"] == 2, str(st))
    check("统计文件=4", st["total"] == 4, str(st))
    store.delete_batch(vid)
    check("删批次后只剩 1", store.stats()["batches"] == 1)
    store.clear()
    check("清空后为 0", store.stats()["total"] == 0)

    print("9) 写死的边界")
    check("空任务不炸", store.begin_batch([]) != "")
    check("human_size", (human_size(0), human_size(1024), human_size(5 * 1024 * 1024))
          == ("0 B", "1.0 KB", "5.0 MB"), str((human_size(0), human_size(1024))))
    store.close()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
