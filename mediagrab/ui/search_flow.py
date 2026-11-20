"""顶栏搜索框的路由。

三个页面共用同一个输入框，但语义不同：结果页按标题过滤卡片，历史页查
数据库，嗅探页过滤已抓到的资源。以前嗅探页是直接禁用的 —— 抓了几十条
之后只能靠类型筛选硬翻。

加上嗅探搜索之后，「切页要重置、各页关键词不能串」这件事需要有个统一的
地方，否则三处各写一遍，迟早会有一处忘了改。
"""

from __future__ import annotations


class SearchFlowMixin:
    def _reset_search(self, key: str) -> None:
        """切页时清空输入框。

        不清的话，从嗅探切到历史会带着上一页的关键词，列表看着像空的，
        用户会以为数据丢了。
        """
        self._query = ""
        if self.search.text():
            self.search.blockSignals(True)      # 免得 clear 触发一次多余的 reload
            self.search.clear()
            self.search.blockSignals(False)
        self.sniff_view.set_query("")

    def _on_search(self, text: str) -> None:
        self._query = text.strip()
        if self._filter == "history":
            self.history_view.reload(self._query)
        elif self._filter == "sniff":
            self.sniff_view.set_query(self._query)
        elif self._filter != "queue":
            self._apply_filter()
