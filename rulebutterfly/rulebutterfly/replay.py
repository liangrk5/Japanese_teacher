"""把事件日志打包成一个可以离线打开的 HTML 复盘文件（复用同一套 3D 前端，three.js 也内联进去）。"""

from __future__ import annotations

import json
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def build_replay(events: list[dict], title: str = "只改一条规则 · 复盘") -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    three = (STATIC / "vendor" / "three.module.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    data = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("<title>只改一条规则</title>", f"<title>{title}</title>")
    html = html.replace('<link rel="stylesheet" href="static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace(
        '<script src="static/app.js"></script>',
        f'<script id="three-src" type="text/plain">{three}</script>\n'
        "<script>window.THREE_URL = URL.createObjectURL(new Blob([document.getElementById('three-src').textContent],"
        " {type: 'text/javascript'}));</script>\n"
        f"<script>window.REPLAY = {data};</script>\n<script>\n{js}\n</script>",
    )
    return html
