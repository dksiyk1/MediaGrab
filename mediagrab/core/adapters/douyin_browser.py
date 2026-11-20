"""抖音的浏览器通道：图集采集、高清档补齐。

抖音的 note 页和 slides 接口都靠页面自己的 JS 算 a_bogus 签名，纯 HTTP 拿不到，
只能开无头浏览器拦响应、读 DOM。这套逻辑和「HTTP 接口解析」的失败模式完全不同
（一个是风控/签名，一个是选图规则），放在适配器里会让文件过千行，所以单独拆。

复用方式用 Mixin：这些方法要读 `self.ctx`、`self._headers()`、`self._browser_cookies`，
拆成独立对象就得把半个适配器传进来。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 关键：抖音分享页对 PC UA 会重定向到 SPA 首页（无数据），
# 只有移动端 UA 才会返回带 __ROUTER_DATA 的 H5 页面
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 在 note 页里收集作品图。
# 页面上混着头像、推荐流缩略图、站内图标，还有右侧"相关推荐"卡片 ——
# 后者尤其坑：它用的是**同一个存储桶**、而且加载的是原图（naturalWidth 2000+），
# 只按尺寸过滤完全挡不住。真正的作品图在 swiper 轮播的 slide 里，
# 所以第一优先级是限定容器，取不到才退回全页规则。
# 同一张图会挂在 p3/p9 多个 host 上，并在 slide 里出现两份（展示层 + 放大层），
# 用 path 里 `~` 之前的部分做唯一键，取面积最大的那份。
_JS_COLLECT_GALLERY = """
() => {
  const hard = ['image-cut', 'aweme-avatar', '/obj/'];
  const pick = (nodes) => {
    const m = new Map();
    for (const e of nodes) {
      const s = e.currentSrc || e.src || '';
      if (!s.includes('douyinpic')) continue;
      if (hard.some(b => s.includes(b))) continue;
      if ((e.naturalWidth || 0) < 600) continue;
      const base = s.split('?')[0].split('~')[0];
      const area = (e.naturalWidth || 0) * (e.naturalHeight || 0);
      const prev = m.get(base);
      if (!prev || area > prev.area) {
        m.set(base, {u: s, w: e.naturalWidth, h: e.naturalHeight, area: area});
      }
    }
    return [...m.values()];
  };

  let images = pick(document.querySelectorAll('[class*="dySwiperSlide"] img'));
  if (!images.length) {
    images = pick(document.images);
    const strict = images.filter(v => v.u.includes('/tos-cn-i-'));
    if (strict.length) images = strict;
  }

  const md = document.querySelector('meta[name="description"]');
  let desc = md ? md.content : (document.title || '').replace(/\\s*-\\s*抖音\\s*$/, '');
  desc = (desc || '').replace(/\\s+/g, ' ')
                     .replace(/\\s*-\\s*[^-]{0,80}于\\d{8}发布在抖音.*$/, '')
                     .trim();
  let author = '';
  const a = document.querySelector('a[href^="/user/"]');
  if (a) author = (a.innerText || '').trim();
  if (!author) {
    const av = document.querySelector('img[src*="aweme-avatar"]');
    if (av) author = (av.alt || '').trim();
  }
  return {
    images: images.map(v => ({u: v.u, w: v.w, h: v.h})),
    desc: desc,
    author: author,
  };
}
"""

# 等到作品图出来（或确认这页根本不渲染大图，比如视频页）
_JS_HAS_GALLERY = """
() => {
  for (const e of document.querySelectorAll('[class*="dySwiperSlide"] img')) {
    if ((e.naturalWidth || 0) >= 600) return true;
  }
  return false;
}
"""



def _extract_braced(text: str, start: int) -> Optional[str]:
    idx = text.find("{", start)
    if idx < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(idx, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[idx : i + 1]
    return None


class DouyinBrowserMixin:
    def _video_detail_via_browser(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """打开视频页，拦截 `web/aweme/detail` 响应拿完整作品数据。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        captured: Dict[str, Any] = {}

        def on_response(resp) -> None:
            if "web/aweme/detail" not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return
            det = data.get("aweme_detail")
            if det and str(det.get("aweme_id")) == str(aweme_id):
                captured["item"] = det

        target = f"https://www.douyin.com/video/{aweme_id}"
        # 正常情况下详情接口 3 秒内就到；页面抖动时可能拖到 10 秒以上。
        # 这里给足一轮 14 秒、不做整轮重试 —— 实测重试很少能救回来，
        # 反而把最差耗时从 14 秒拉到 20 秒以上
        try:
            with sync_playwright() as p:
                browser = None
                for channel in ("msedge", "chrome"):
                    try:
                        browser = p.chromium.launch(
                            channel=channel, headless=True,
                            args=["--disable-blink-features=AutomationControlled"],
                        )
                        break
                    except Exception:
                        continue
                if browser is None:
                    return None
                ctx = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1440, "height": 900},
                    user_agent=self.ctx.user_agent or UA,
                )
                page = ctx.new_page()
                page.on("response", on_response)
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                for _ in range(28):        # 最多 14 秒，抓到就走
                    if captured.get("item"):
                        break
                    page.wait_for_timeout(500)
                browser.close()
        except Exception:
            pass

        return captured.get("item")

    # ---------- 图文：note 页 ----------

    def _gallery_via_note_page(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """图文作品：打开 www.douyin.com/note/{id}，从渲染后的 DOM 取图。

        为什么不能直接调接口：
        - 图文详情接口 `web/api/v2/aweme/slidesinfo/` 只认"幻灯片"类型，
          普通图文（note）请求它会被回 `filter_list.reason = 8`，
          看起来像"内容不可用"，其实只是问错了接口 —— 换成 note 页就正常
        - note 页的详情数据由前端带 a_bogus 签名自己拉，我们截不到，
          但页面会把作品图渲染成 <img>，直接读 DOM 又稳又不依赖签名

        另有一个坑：图片 CDN 是 `*-sign.douyinpic.com`，请求必须带本次浏览器
        会话的 Cookie，否则一律 403。所以这里顺手把 Cookie 存下来给下载器用。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        target = f"https://www.douyin.com/note/{aweme_id}"
        data: Dict[str, Any] = {}

        # 抖音风控会抖动，整轮重试一次基本就稳
        for attempt in range(2):
            try:
                with sync_playwright() as p:
                    browser = None
                    for channel in ("msedge", "chrome"):
                        try:
                            browser = p.chromium.launch(
                                channel=channel, headless=True,
                                args=["--disable-blink-features=AutomationControlled"],
                            )
                            break
                        except Exception:
                            continue
                    if browser is None:
                        return None
                    ctx = browser.new_context(
                        locale="zh-CN",
                        viewport={"width": 1440, "height": 900},
                        user_agent=self.ctx.user_agent or UA,
                    )
                    page = ctx.new_page()
                    try:
                        page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass

                    # 作品不存在/已删除时，抖音会把 /note/{id} 重定向到首页，
                    # 而首页也有 dySwiperSlide 轮播图 —— 不校验落点就会把
                    # 推荐内容当成目标作品返回（伪造 ID 也能"解析成功"）
                    if aweme_id not in (page.url or ""):
                        return None

                    for _ in range(25):        # 最多 12.5 秒
                        try:
                            if page.evaluate(_JS_HAS_GALLERY):
                                break
                        except Exception:
                            pass
                        page.wait_for_timeout(500)

                    # 首屏出图后页面还会做一轮替换：先把 webp 缩略版铺上去，
                    # 随后换成 ~noop.jpeg 原图（同尺寸下体积大得多）。
                    # 固定 sleep 要么等不够、要么白等，改成盯着图片列表直到它稳定。
                    last_sig, stable = "", 0
                    for _ in range(20):        # 最多 10 秒
                        data = page.evaluate(_JS_COLLECT_GALLERY) or {}
                        sig = "|".join(it["u"] for it in (data.get("images") or []))
                        if sig and sig == last_sig:
                            stable += 1
                            if stable >= 2:    # 连续两次没变化，认为加载完了
                                break
                        else:
                            stable = 0
                        last_sig = sig
                        page.wait_for_timeout(500)

                    data["page_title"] = (page.title() or "").strip()
                    cookies = {c["name"]: c["value"] for c in ctx.cookies()}
                    browser.close()
            except Exception:
                data = {}

            if data.get("images"):
                self._browser_cookies = cookies
                break
            if attempt == 0:
                import time as _t

                _t.sleep(1.0)

        images = data.get("images") or []
        if not images:
            return None

        # 作品不存在时抖音不报错、不改 URL，而是把推荐流/广告塞进这个页面：
        # 图抓得到、文案也有（"#某活动 11.13 公测"），只有两处是空的 ——
        #   · 作者昵称（作品页必然显示作者，广告位没有）
        #   · 页面标题（真作品是「文案 - 抖音」）
        # 用作者为主、标题为辅，两个都空才判定这不是作品页。
        author = (data.get("author") or "").strip()
        if not author:
            return None

        desc = (data.get("desc") or "").strip()
        if not desc and len(images) < 2:
            return None

        return {
            "aweme_id": aweme_id,
            "desc": desc or "抖音图文",
            "author": {"nickname": data.get("author") or ""},
            "images": [{"url_list": [it["u"]]} for it in images],
            "video": {},
        }

    def _gallery_via_browser(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """图文作品：用真实浏览器打开 slides 页，拦截 slidesinfo 接口的响应。

        为什么必须这样：
        - 图文数据接口 `web/api/v2/aweme/slidesinfo/` 需要 a_bogus 签名（JS 生成）
        - aweme/v1/aweme/detail/ 已对所有作品关闭
        - aweme/v1/feed/ 只返回视频，图文作品不在这个通道
        浏览器里页面的 JS SDK 会自动算签名，我们只管拦结果。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        captured: Dict[str, Any] = {}

        def on_response(resp) -> None:
            if "slidesinfo" not in resp.url and "aweme/detail" not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return

            # 平台过滤：filter_list 里带 reason 说明这条作品被限制了
            for f in data.get("filter_list") or []:
                if str(f.get("aweme_id")) == str(aweme_id):
                    captured["filter"] = f.get("reason")

            details = data.get("aweme_details") or []
            if not details:
                one = data.get("aweme_detail")
                if one:
                    details = [one]
            for d in details:
                if str(d.get("aweme_id")) == str(aweme_id):
                    captured["item"] = d
                    return

        target = f"https://www.iesdouyin.com/share/slides/{aweme_id}/"
        # 抖音偶尔让第一轮拿不到数据（风控抖动），同一页面刷新一次基本就好
        for attempt in range(2):
            try:
                with sync_playwright() as p:
                    browser = None
                    for channel in ("msedge", "chrome"):
                        try:
                            browser = p.chromium.launch(
                                channel=channel, headless=True,
                                args=["--disable-blink-features=AutomationControlled"],
                            )
                            break
                        except Exception:
                            continue
                    if browser is None:
                        return None
                    ctx = browser.new_context(
                        locale="zh-CN",
                        viewport={"width": 414, "height": 896},
                        user_agent=UA_MOBILE,
                    )
                    page = ctx.new_page()
                    page.on("response", on_response)
                    try:
                        # 不用 networkidle：抖音页面有心跳请求，几乎永远不会 idle，
                        # 会白白等到 45 秒超时
                        page.goto(target, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    # 页面 JS 算完 a_bogus 才会发 slidesinfo，抓到就走，不必等死。
                    # 拿到 filter 标记也立刻停：平台已明确拒绝，再等再重试都是浪费
                    for _ in range(24):        # 单轮最多 12 秒
                        if captured.get("item") or captured.get("filter") is not None:
                            break
                        page.wait_for_timeout(500)
                    browser.close()
            except Exception:
                pass

            if captured.get("item") or captured.get("filter") is not None:
                break
            if attempt == 0:
                import time as _t

                _t.sleep(1.2)

        self._last_filter_reason = captured.get("filter")
        return captured.get("item")

