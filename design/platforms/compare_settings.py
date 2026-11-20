"""「设置窗口：原样式 → 优化后」对比板。

两张都是真实窗口截图（含系统标题栏）：
  before  改之前抓的存档，600 × 987
  after   现在，720 × 899（通用页封顶，另两页 520）
"""
from __future__ import annotations

import base64
import os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE))
SHOTS = os.path.join(ROOT, "_shots")

BEFORE = os.path.join(SHOTS, "settings_before.png")          # 922 × 1537
AFTER = os.path.join(SHOTS, "settings_tuned_general.png")    # 1102 × 1405

W_BEFORE, H_BEFORE = 500, 833
W_AFTER, H_AFTER = 600, 765


def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


CSS = """
:root{
  --paper:#F2F2EF; --card:#fff; --ink:#16161A; --ink2:#5C5C63; --ink3:#8E8E93;
  --line:#DCDCD6; --line2:#EBEBE5; --red:#C42B1C; --blue:#0067C0;
  --font:"Segoe UI Variable Display","Segoe UI","Microsoft YaHei UI","PingFang SC",sans-serif;
  --mono:Consolas,"SF Mono",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{width:1240px}
body{width:1240px;font-family:var(--font);background:var(--paper);color:var(--ink);
  -webkit-font-smoothing:antialiased;padding:40px 44px 34px}

.eyebrow{font-size:10px;letter-spacing:.22em;color:var(--ink3);font-weight:600}
h1{font-size:25px;font-weight:600;letter-spacing:-.4px;margin-top:9px}
.dek{font-size:12.5px;color:var(--ink2);margin-top:9px;line-height:1.65;max-width:780px}
.meta{margin-left:auto;text-align:right;font-size:10.5px;color:var(--ink3);line-height:1.85}
.meta b{display:block;color:var(--ink2);font-weight:500;font-size:11px}
.head{display:flex;align-items:flex-start;padding-bottom:22px;border-bottom:1px solid var(--line)}

.cols{display:flex;gap:40px;margin-top:26px;align-items:flex-start}
.chead{display:flex;align-items:baseline;gap:9px;margin-bottom:10px}
.chead .tag{font-size:9.5px;font-weight:600;letter-spacing:.12em;padding:2px 6px;border-radius:3px}
.before .tag{background:#E6E6E0;color:#55555C}
.after .tag{background:var(--blue);color:#fff}
.chead .nm{font-size:13px;font-weight:600}
.chead .dim{margin-left:auto;font-size:10.5px;color:var(--ink3);font-family:var(--mono)}

.stage{position:relative;border-radius:8px;overflow:hidden;background:#fff;
  box-shadow:0 10px 30px rgba(16,18,24,.16),0 1px 3px rgba(16,18,24,.10)}
.stage img{display:block}

.notes{margin-top:14px}
.notes li{list-style:none;display:flex;gap:8px;font-size:11.5px;line-height:1.6;
  color:var(--ink2);padding:7px 0;border-bottom:1px solid var(--line2)}
.notes li:last-child{border-bottom:none}
.notes i{flex:0 0 15px;height:15px;border-radius:50%;font-style:normal;font-size:9px;
  font-weight:700;display:flex;align-items:center;justify-content:center;margin-top:2px}
.before .notes i{background:#F6E7E5;color:var(--red)}
.after .notes i{background:#E4EFF9;color:var(--blue)}
.notes b{color:var(--ink);font-weight:600}

table{width:100%;border-collapse:collapse;margin-top:32px;font-size:11.5px}
th{text-align:left;font-size:9.5px;letter-spacing:.14em;color:var(--ink3);font-weight:600;
  padding:0 12px 8px;border-bottom:1px solid var(--ink);text-transform:uppercase}
td{padding:9px 12px;border-bottom:1px solid var(--line2);vertical-align:top;line-height:1.55}
td:first-child{width:168px;color:var(--ink2)}
td:nth-child(2){width:330px;color:#6B6B71}
td .mono{font-family:var(--mono);font-size:10.5px;background:#F1F1EC;
  padding:1px 4px;border-radius:3px}

.foot{display:flex;gap:14px;margin-top:26px}
.vd{flex:1;border-top:2px solid var(--line);padding-top:11px;font-size:11px;
  color:var(--ink2);line-height:1.62}
.vd b{display:block;font-size:11.5px;color:var(--ink);margin-bottom:4px;font-weight:600}
.vd.a{border-top-color:var(--blue)}
"""


def build() -> str:
    before, after = b64(BEFORE), b64(AFTER)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>

<div class="head">
  <div>
    <div class="eyebrow">SETTINGS · 设置窗口</div>
    <h1>样式不动，只修尺寸与密度</h1>
    <div class="dek">
      保留系统标题栏和标签页——设置是低频模态窗口，用原生边框反而和系统设置一致。
      要修的是它自己被内容撑到 987px、行距松散、数字框被挤这三件事。
    </div>
  </div>
  <div class="meta">
    <b>2026-11-17</b>
    两张都是真实窗口截图<br>
    均含系统标题栏<br>
    同一份「通用」页
  </div>
</div>

<div class="cols">

  <div class="col before" style="width:{W_BEFORE}px">
    <div class="chead">
      <span class="tag">BEFORE</span>
      <span class="nm">由内容撑开</span>
      <span class="dim">600 × 987</span>
    </div>
    <div class="stage" style="width:{W_BEFORE}px;height:{H_BEFORE}px">
      <img src="data:image/png;base64,{before}" width="{W_BEFORE}">
    </div>
    <ul class="notes">
      <li><i>1</i><span>三个标签页取最高的那个当窗口高度：<b>987px</b>，而屏幕可用只有 1019px。
        加标题栏几乎顶满，任务栏上沿都贴住了。</span></li>
      <li><i>2</i><span>「同时下载」的数字框被压到 <b>40px</b>，「1 路」的后缀被上下箭头盖住。</span></li>
      <li><i>3</i><span>表单行距 12px，17 行堆出 <b>68px 的多余空气</b>，看着松散。</span></li>
      <li><i>4</i><span>保存按钮被推到窗口最底部，小屏上会被切掉。</span></li>
    </ul>
  </div>

  <div class="col after" style="width:{W_AFTER}px">
    <div class="chead">
      <span class="tag">AFTER</span>
      <span class="nm">跟着当前页走</span>
      <span class="dim">720 × 899</span>
    </div>
    <div class="stage" style="width:{W_AFTER}px;height:{H_AFTER}px">
      <img src="data:image/png;base64,{after}" width="{W_AFTER}">
    </div>
    <ul class="notes">
      <li><i>1</i><span>高度按<b>当前页内容</b>算，封顶在可用高度减 120：通用 899 并出现滚动条，
        登录态 / 网络缩到 520。</span></li>
      <li><i>2</i><span>数字框 <b>92px</b>，「1 路」和箭头各归各位。</span></li>
      <li><i>3</i><span>行距 <b>9px</b> + 提示文字统一走 <span class="mono">_hint()</span>，
        同样的字段少 88px 高。</span></li>
      <li><i>4</i><span>底栏在标签页之外，<b>任何屏幕尺寸都看得见</b>；内容超了在卡片内滚动。</span></li>
    </ul>
  </div>

</div>

<table>
  <tr><th>项</th><th>改之前</th><th>现在</th></tr>
  <tr><td>窗口尺寸</td><td>600 × 987，<span class="mono">resize()</span> 被内容顶掉</td>
      <td>720 × <span class="mono">min(900, 可用高-120)</span>，按当前页内容定</td></tr>
  <tr><td>切标签页</td><td>高度不变（固定取最高页）</td>
      <td>高度跟着变，保持左上角不动，压到屏幕底才上移</td></tr>
  <tr><td>内容溢出</td><td>无滚动，直接顶满屏幕</td>
      <td>每页 QScrollArea，超了在卡片内滚动</td></tr>
  <tr><td>表单行距</td><td class="mono">12px</td><td class="mono">9px</td></tr>
  <tr><td>数字框</td><td class="mono">64px</td><td class="mono">92px</td></tr>
  <tr><td>提示文字</td><td>7 处各自 new QLabel + 设样式</td>
      <td>统一 <span class="mono">_hint(text)</span>，改一处全变</td></tr>
</table>

<div class="foot">
  <div class="vd a">
    <b>样式一行没动</b>
    系统标题栏、标签页、白卡片、开关、按钮，全部保持原样。
    改的只有尺寸计算、行距、和一个数字框的宽度。
  </div>
  <div class="vd">
    <b>顺手统一了提示文字</b>
    7 处提示原来各自 <span class="mono">new QLabel</span> 三行样板，
    现在一行 <span class="mono">_hint()</span>。文案一个字没改。
  </div>
  <div class="vd">
    <b>业务逻辑零改动</b>
    字段顺序、保存逻辑、Esc 取消全部原样；
    测试仍在验证模板回填、开关回填、限速回填。
  </div>
</div>

</body></html>
"""


def main() -> None:
    html = os.path.join(BASE, "settings-compare.html")
    with open(html, "w", encoding="utf-8") as f:
        f.write(build())

    out = os.path.join(BASE, "renders", "settings_compare.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shoot(html, out)
    print("ok", out, os.path.getsize(out) // 1024, "KB")


def shoot(html: str, out: str) -> None:
    """整页截图：full_page 按 scrollHeight 裁，不留白。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1240, "height": 900},
                                device_scale_factor=1.5)
        page.goto("file:///" + html.replace("\\", "/"))
        page.wait_for_timeout(450)
        page.screenshot(path=out, full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
