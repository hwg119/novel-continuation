"""只读历史工具：可被任意小说智能体调用，不依赖规划提示词或模型。"""
from pathlib import Path
import re


class HistoryTools:
    def __init__(self, project_dir, before_chapter):
        self.files = {}
        for path in (Path(project_dir) / "chapters").glob("chapter_*.txt"):
            match = re.fullmatch(r"chapter_(\d+)\.txt", path.name)
            if match and int(match[1]) < int(before_chapter):
                self.files[int(match[1])] = path
        self.cache = {}

    def _body(self, number):
        if number not in self.files:
            raise ValueError("该历史章节不存在或不在允许范围")
        if number not in self.cache:
            self.cache[number] = self.files[number].read_text(encoding="utf-8")
        return self.cache[number]

    def _row(self, number, offset, size, **extra):
        body = self._body(number)
        text = body[offset:offset + size]
        return {"chapter": number, "offset": offset, "text": text,
                "next_offset": offset + len(text), "total_chars": len(body), **extra}

    def execute(self, call, sources):
        if not isinstance(call, dict):
            raise ValueError("工具参数必须是对象")
        tool = call.get("tool")
        if tool == "expand_source":
            source = next((row for row in sources if row["id"] == call.get("source_id")), None)
            if not source or "chapter" not in source or "offset" not in source:
                raise ValueError("只能扩展工具返回的历史正文片段")
            direction = call.get("direction", "after")
            if direction not in {"before", "after", "around"}:
                raise ValueError("direction 必须为 before、after 或 around")
            size = min(2400, max(1, int(call.get("size", 1600))))
            begin, end = source["offset"], source["next_offset"]
            if direction == "before":
                offset = max(0, begin - size)
                size = begin - offset
            elif direction == "after":
                offset = end
            else:
                offset = max(0, begin - size // 2)
            return [self._row(source["chapter"], offset, size, expanded_from=source["id"])]
        if tool == "read_chapter":
            number = int(call.get("chapter"))
            return [self._row(number, max(0, int(call.get("offset", 0))), 2400)]
        if tool == "locate_quote":
            source = next((row for row in sources if row["id"] == call.get("source_id")), None)
            quote = str(call.get("quote") or "")
            offset = source["text"].find(quote) if source and quote else -1
            return {"source_id": call.get("source_id"), "quote": quote,
                    "found": offset >= 0, "offset_in_source": offset}
        if tool != "search_history":
            raise ValueError("不支持的查阅工具")
        values = call.get("terms")
        if not isinstance(values, list):
            raise ValueError("terms 必须是关键词数组")
        terms = list(dict.fromkeys(t.strip()[:80] for t in values if isinstance(t, str) and t.strip()))[:5]
        if not terms:
            raise ValueError("搜索关键词不能为空")
        start = int(call.get("chapter_start", 1))
        end = int(call.get("chapter_end", max(self.files, default=0)))
        if call.get("chapter") is not None:
            start = end = int(call["chapter"])
            self._body(start)  # 单章查询也遵守历史边界。
        page = max(0, int(call.get("page", 0)))
        hits = []
        for number in sorted(self.files):
            if not start <= number <= end:
                continue
            body = self._body(number)
            positions = set()
            for term in terms:
                cursor = 0
                while True:
                    pos = body.find(term, cursor)
                    if pos < 0:
                        break
                    positions.add(pos)
                    cursor = pos + len(term)
            for pos in sorted(positions):
                row = self._row(number, max(0, pos - 350), 1100)
                row["matched_terms"] = [term for term in terms if term in row["text"]]
                hits.append(row)
        hits.sort(key=lambda row: (len(row["matched_terms"]), row["chapter"]), reverse=True)
        selected = []
        # 合并命中窗口的效果：同章重叠内容不占据多个名额，优先保留高相关窗口。
        for row in hits:
            if any(row["chapter"] == old["chapter"] and
                   row["offset"] < old["next_offset"] and old["offset"] < row["next_offset"]
                   for old in selected):
                continue
            selected.append(row)
        # 首轮给不同章节机会，再补充同章不重叠窗口。
        distinct, remaining, seen = [], [], set()
        for row in selected:
            if row["chapter"] not in seen:
                distinct.append(row)
                seen.add(row["chapter"])
            else:
                remaining.append(row)
        ranked = distinct + remaining
        result = ranked[page * 4:(page + 1) * 4]
        for row in result:
            row.update(search_page=page, total_windows=len(ranked),
                       next_page=page + 1 if (page + 1) * 4 < len(ranked) else None,
                       search_terms=terms)
        return result
