#!/usr/bin/env python3
"""Regenerate apps/web-demo/hosted/ and hookpilot-demo-standalone.html from the IIFE build.

    cd apps/web-demo && npx vite build --config vite.iife.config.ts && python3 scripts/build_hosted.py

hosted/: app.js split into 80000-char chunks app.part1..N.js, each
    window.__hp=(window.__hp||[]);window.__hp.push(<JSON string>);
index.html loads ./app.css, the part scripts in order, then (0,eval)(window.__hp.join("")).
Stale app.part*.js files are deleted. The standalone file inlines the CSS and the script (classic).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/iife-dist"
CHUNK = 80000


def main() -> int:
    js = open(os.path.join(SRC, "app.js"), encoding="utf-8").read()
    css = open(os.path.join(SRC, "app.css"), encoding="utf-8").read()
    html = open(os.path.join(SRC, "index.html"), encoding="utf-8").read()
    # drop vite's module script + stylesheet tags; we add our own
    base = re.sub(r'\s*<script type="module"[^>]*></script>', "", html)
    base = re.sub(r'\s*<link rel="stylesheet"[^>]*>', "", base)
    assert "</head>" in base and "</body>" in base

    out = os.path.join(APP, "hosted")
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "app.part*.js")):
        os.remove(f)
    parts = [js[i:i + CHUNK] for i in range(0, len(js), CHUNK)]
    for k, p in enumerate(parts, 1):
        with open(os.path.join(out, f"app.part{k}.js"), "w", encoding="utf-8", newline="\n") as f:
            f.write("window.__hp=(window.__hp||[]);window.__hp.push(" + json.dumps(p) + ");")
    with open(os.path.join(out, "app.css"), "w", encoding="utf-8", newline="\n") as f:
        f.write(css)
    tags = "\n".join(f'<script src="./app.part{k}.js"></script>' for k in range(1, len(parts) + 1))
    hosted = base.replace("</head>", '\n    <link rel="stylesheet" href="./app.css">\n  </head>', 1)
    hosted = hosted.replace("</body>", tags + '\n<script>(0,eval)(window.__hp.join(""));</script>\n</body>', 1)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(hosted)

    inline_js = js.replace("</script", "<\\/script")
    sa = base.replace("</head>", "\n    <style>" + css + "</style>\n  </head>", 1)
    sa = sa.replace("</body>", "<script>" + inline_js + "</script>\n</body>", 1)
    with open(os.path.join(APP, "hookpilot-demo-standalone.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(sa)
    print(f"hosted/: {len(parts)} parts, app.js {len(js)} chars; standalone {len(sa)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
