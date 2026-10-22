"""把事件日志打包成一个可以离线打开的 HTML 复盘文件（复用观战页的渲染器）。"""

from __future__ import annotations

import json
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def build_replay(events: list[dict], title: str = "AI 狼人杀复盘") -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    data = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    js = js.replace("</script", "<\\/script")
    html = html.replace("<title>AI 狼人杀</title>", f"<title>{title}</title>")
    html = html.replace('<link rel="stylesheet" href="static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace(
        '<script src="static/app.js"></script>',
        f"<script>window.REPLAY = {data};</script>\n<script>\n{js}\n</script>",
    )
    return html
