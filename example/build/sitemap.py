# -*- coding: utf-8 -*-
"""Sitemapa ukázkového webu: www/sitemap.xml ze stránek v content/pages.json bez noindex.
Zapíše soubor, jen když se změnil (publikace z editoru ho bere jako odvozený soubor).

  python build/sitemap.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web as W  # noqa: E402

def sitemap(data):
    urls = []
    for slug in sorted(data.manifest(), key=lambda s: (s != "index", s)):
        if "noindex" in W.metas(data.page(slug)).get("robots", ""):
            continue
        urls.append("  <url><loc>%s</loc></url>\n" % W.page_url(slug, data))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % "".join(urls))


def main():
    data = W.Data()
    out = data.root / data.source / "sitemap.xml"
    text = sitemap(data)
    old = out.read_text(encoding="utf-8") if out.is_file() else None
    if text != old:
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    print("sitemap: %d adres%s" % (text.count("<url>"), "" if old != text else ", beze změny"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
