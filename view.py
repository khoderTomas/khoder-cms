# -*- coding: utf-8 -*-
"""HTML pro editor: náhled (stránka jak na webu, bez GTM) a editační pohled (bez JS webu,
s anotací bloků pro editor.js).

Pod base (v adminu /web) se odkazy na stránky webu (/<slug>) přepíší na <base>/<slug>, ať klik
v náhledu zůstane v editoru. Styly, skripty a obrázky stránky jsou relativní, takže je servíruje
editor pod base sám. Editační pohled pouští jen editor.js (nonce, CSP v cms/app.py)."""
import html as _html
import re

import adapter as AD
import blocks as B
import classify as C

_GTM = re.compile(r"[ \t]*<!-- Google Tag Manager -->.*?<!-- End Google Tag Manager -->\s*", re.S)
_GTM_NS = re.compile(r"[ \t]*<!-- Google Tag Manager \(noscript\) -->.*?"
                     r"<!-- End Google Tag Manager \(noscript\) -->\s*", re.S)
# skripty webu (app.js, sections.js, inline skripty včetně motivu v hlavičce) mění DOM; JSON data zůstanou
_SCRIPT = re.compile(r'[ \t]*<script\b(?![^>]*\btype="application/(?:ld\+)?json")[^>]*>.*?</script>\s*', re.S)
_DETAILS = re.compile(r"<details\b([^>]*)>")
_HREF = re.compile(r'(\bhref=")(/[^"]*)(")')


def strip_gtm(html):
    return _GTM_NS.sub("", _GTM.sub("", html))


def rebase(html, base, slugs):
    """Odkazy na stránky webu (href="/<slug>", "/" = homepage) přesměruje pod base editoru."""
    if not base:
        return html

    def fix(m):
        url = m.group(2)
        cut = min([i for i in (url.find("?"), url.find("#")) if i >= 0] or [len(url)])
        path, rest = url[:cut], url[cut:]
        slug = "index" if path == "/" else path[1:-5] if path.endswith(".html") else path[1:]
        if slug in slugs:
            return m.group(1) + base + "/" + slug + rest + m.group(3)
        return m.group(0)
    return _HREF.sub(fix, html)


def preview(src, base="", slugs=(), ui_css=False):
    """Stránka jak na webu (se skripty webu), jen bez GTM a s tlačítkem zpět do editoru (ui_css =
    hostitel podává sadu prvků editoru jako <base>/_admin/ui.css)."""
    css = "".join('<link rel="stylesheet" href="%s/_admin/%s">\n' % (base, f)
                  for f in (("ui.css",) if ui_css else ()) + ("editor.css",))
    back = '<a class="ke-ui k-btn k-btn--primary ke-backedit" href="?e=1">Upravit texty</a>\n'
    html = rebase(strip_gtm(src), base, slugs)
    return html.replace("</head>", css + "</head>", 1).replace("</body>", back + "</body>", 1)


def _details(m):
    attrs = re.sub(r'\s+name="[^"]*"', "", m.group(1))
    if not AD.RULES.keep_closed(attrs) and not re.search(r"\bopen\b", attrs):
        attrs += " open"
    return "<details%s>" % attrs


def edit_view(src, slug, kind, page_lock=None, prov=None, source="", base="", nonce=None, metas=None, slugs=(),
              site_css=False, part_lock=None, sprite="", ui_css=False):
    """page_lock = důvod, proč je zamčená celá stránka (owner bez odemčení…).
    prov = proveniance generované stránky: bloky bez zdroje se zamknou, sdílené
    dostanou data-ke-shared/data-ke-pages/data-ke-file. source = soubor, kam se ukládá.
    base = cesta editoru (admin: /web), nonce = jediný povolený skript (editor.js), metas = další
    <meta name="ke-…"> pro klienta (role, odemčení), site_css = styly editačního pohledu pro prvky
    webu (<base>/_admin/site.css, cms.toml [editor] css), part_lock = proč uživatel nesmí měnit sdílené
    bloky menu a patičky (role), sprite = ikony editoru (id ke-i-…), ui_css = hostitel podává sadu
    prvků editoru (<base>/_admin/ui.css, tokeny jen v .ke-ui). Zámky bloků dává classify.blocks
    a texty mimo bloky (panel Další texty) classify.attr_nodes, stejně jako report pokrytí
    (cms/coverage.py)."""
    root = B.parse(src)
    bl, extra = C.blocks(src, page_lock, prov, root=root, part_lock=part_lock)
    marks = C.attr_marks(C.attr_nodes(src, kind, page_lock, part_lock, root=root))
    html = rebase(strip_gtm(B.annotate(src, bl, extra, marks)), base, slugs)
    i = html.index("</head>")
    head, body = _SCRIPT.sub("", html[:i]), html[i:]
    body = _SCRIPT.sub("", body)
    body = _DETAILS.sub(_details, body)
    meta = ('<meta name="ke-slug" content="%s"><meta name="ke-kind" content="%s">'
            '<meta name="ke-page" content="%s"><meta name="ke-lock" content="%s">'
            '<meta name="ke-source" content="%s"><meta name="ke-base" content="%s">%s\n'
            '%s<link rel="stylesheet" href="%s/_admin/editor.css">\n%s'
            % (slug, kind, B.page_hash(src), B._attr(page_lock or ""), B._attr(source or ""),
               B._attr(base), "".join('<meta name="%s" content="%s">' % (k, B._attr(str(v)))
                                       for k, v in (metas or {}).items()),
               '<link rel="stylesheet" href="%s/_admin/ui.css">\n' % base if ui_css else "", base,
               '<link rel="stylesheet" href="%s/_admin/site.css">\n' % base if site_css else ""))
    body = body.replace("</body>", '%s<script src="%s/_admin/editor.js"%s defer></script>\n</body>'
                        % (sprite + "\n" if sprite else "", base, ' nonce="%s"' % _html.escape(nonce) if nonce else ""), 1)
    return head + meta + body
