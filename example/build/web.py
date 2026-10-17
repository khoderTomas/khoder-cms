# -*- coding: utf-8 -*-
"""Build ukázkového webu: kroky buildu stránky a kontroly. Jediné místo s pořadím kroků pro build
z příkazové řádky (build/build.py) i pro editor (adaptér build/cms_adapter.py: Site.inject, Site.check).
Jen standardní knihovna.

Stránky jsou ruční HTML ve www/ a build je přepisuje na místě. Kroky render():
  partials  sloty <!-- PARTIAL:header --> a <!-- PARTIAL:footer --> (značky na vlastních řádcích bez
            odsazení) dostanou šablonu z content/partials/<jméno>.html; odkaz hlavičky na stránku samu
            dostane aria-current="page" (v šabloně nebývá)
  entity    blok JSON-LD s "@type": "LocalBusiness" je objekt business z content/site.json, přepsaný
            hodnotami stránky z pages.<slug> (údaje o vás v panelu SEO)
  atoms     prvek s data-site="<klíč>" nese údaj z business: text, u odkazu i href (tel:, mailto:);
            address je ulice, PSČ a město

Kontroly check() hlídají, že stránka odpovídá datům (render ji nezmění) a projde SEO: jazyk, title
a description (délka, jedinečnost), jeden h1, canonical a og u indexovaných stránek, JSON-LD parsuje,
FAQPage má tolik otázek jako viditelné FAQ, obrázky mají alt a rozměry, interní odkazy vedou na
stránky z manifestu nebo soubory webu a kontakty v odkazech (tel:, mailto:) sedí na content/site.json.
"""
import html
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "cms.toml"                              # složka stránek, manifest a adresa webu ([deploy])
SITE_FILE = "content/site.json"
PARTIALS = ("header", "footer")
KINDS = frozenset({"handbuilt", "owner"})          # druhy stránek, které nesou hlavičku a patičku
ENTITY_KEY, ENTITY_TYPE = "business", "LocalBusiness"
TITLE_MAX, DESCRIPTION_MAX = 70, 170

SLOT_RE = re.compile(r"^<!-- PARTIAL:([a-z]+) -->\n(.*?)^<!-- /PARTIAL:\1 -->\n", re.S | re.M)
MARK_RE = re.compile(r"<!-- /?PARTIAL:")
LD_RE = re.compile(r'(<script type="application/ld\+json">)(.*?)(</script>)', re.S)
ATOM_RE = re.compile(r'<(a|span|p)\b([^>]*?)\sdata-site="([A-Za-z.]+)"([^>]*)>(.*?)</\1>', re.S)
HREF_RE = re.compile(r'\shref="[^"]*"')
LINK_RE = re.compile(r'<a\b[^>]*\shref="/([a-z0-9-]+)"[^>]*>')
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
META_RE = re.compile(r'<meta (name|property)="([a-z:]+)" content="([^"]*)">')
CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]*)">')
LANG_RE = re.compile(r'<html lang="[a-z]{2}(?:-[A-Z]{2})?">')
H1_RE = re.compile(r"<h1[\s>]")
IMG_RE = re.compile(r"<img\b[^>]*>")
HREF_ALL_RE = re.compile(r'\s(?:href|src)="([^"]*)"')
FAQ_ITEM = '<details class="faq-item"'


class BuildError(Exception):
    """Krok buildu odmítl stránku nebo data (hláška pro člověka)."""


# ── data webu ──
class Data:
    """Data webu z repa root. overlay = {cesta v repu: text} přebije soubory (zkušební sestavení
    v editoru s neuloženou změnou šablony nebo content/site.json)."""

    def __init__(self, root=ROOT, overlay=None):
        self.root = Path(root)
        self.overlay = dict(overlay or {})
        self._cache = {}
        with open(self.root / CONFIG, "rb") as f:
            dep = tomllib.load(f).get("deploy") or {}
        self.source = dep.get("source", "www")
        self.manifest_rel = dep.get("pages", "content/pages.json")
        self.url = (dep.get("site") or "").rstrip("/")

    def text(self, rel):
        if rel in self.overlay:
            return self.overlay[rel]
        if rel not in self._cache:
            with open(self.root / rel, encoding="utf-8", newline="") as f:
                self._cache[rel] = f.read()
        return self._cache[rel]

    def json(self, rel):
        try:
            return json.loads(self.text(rel))
        except ValueError as e:
            raise BuildError("%s nejde přečíst: %s" % (rel, e)) from None

    def manifest(self):
        return self.json(self.manifest_rel)

    def site(self):
        data = self.json(SITE_FILE)
        if not isinstance(data.get(ENTITY_KEY), dict):
            raise BuildError("%s nemá objekt %s" % (SITE_FILE, ENTITY_KEY))
        return data

    def template(self, name):
        rel = partial_rel(name)
        try:
            t = self.text(rel)
        except FileNotFoundError:
            raise BuildError("%s chybí" % rel) from None
        return validate_template(name, t)

    def page(self, slug):
        return self.text("%s/%s.html" % (self.source, slug))


def partial_rel(name):
    return "content/partials/%s.html" % name


def validate_template(name, t):
    rel = partial_rel(name)
    if "\r" in t:
        raise BuildError("%s: konce řádků CRLF, šablona je v LF" % rel)
    if not t.startswith("<%s " % name) or not t.endswith("</%s>\n" % name):
        raise BuildError("%s: šablona je celý <%s>…</%s> a končí novým řádkem" % (rel, name, name))
    if MARK_RE.search(t):
        raise BuildError("%s: značka slotu PARTIAL patří do stránky, ne do šablony" % rel)
    if "aria-current=" in t:
        raise BuildError("%s: aria-current počítá build podle stránky, v šabloně nebývá" % rel)
    return t


# ── kroky buildu ──
def slots(s):
    """[(začátek, konec, jméno)] obsahu slotů PARTIAL na stránce (úseky, které build vkládá ze šablon)."""
    found = list(SLOT_RE.finditer(s))
    if len(MARK_RE.findall(s)) != 2 * len(found):
        raise BuildError("značky PARTIAL nejsou v párech na vlastních řádcích bez odsazení")
    names = [m.group(1) for m in found]
    for n in names:
        if n not in PARTIALS:
            raise BuildError("neznámý slot PARTIAL:%s (známé: %s)" % (n, ", ".join(PARTIALS)))
        if names.count(n) > 1:
            raise BuildError("slot PARTIAL:%s je na stránce %d×" % (n, names.count(n)))
    return [(m.start(2), m.end(2), m.group(1)) for m in found]


def current(t, slug):
    """aria-current="page" odkazu šablony na stránku samu."""
    return LINK_RE.sub(lambda m: m.group(0)[:-1] + ' aria-current="page">' if m.group(1) == slug else m.group(0), t)


def render_partials(s, slug, data):
    out, pos = [], 0
    for a, z, name in slots(s):
        t = data.template(name)
        out += [s[pos:a], current(t, slug) if name == "header" else t]
        pos = z
    return "".join(out) + s[pos:]


def entity_expected(data, slug):
    site = data.site()
    return dict(site[ENTITY_KEY], **(site.get("pages") or {}).get(slug, {}))


def entity_text(data):
    """Text content/site.json, jak ho zapisuje editor (údaje o vás)."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _ld(body):
    try:
        return json.loads(body)
    except ValueError:
        return None


def entity_spans(s):
    """[(začátek, konec)] bloků JSON-LD s objektem entity (plní je build)."""
    out = []
    for m in LD_RE.finditer(s):
        o = _ld(m.group(2))
        if isinstance(o, dict) and o.get("@type") == ENTITY_TYPE:
            out.append(m.span())
    return out


def render_entity(s, slug, data):
    def fill(m):
        o = _ld(m.group(2))
        if not (isinstance(o, dict) and o.get("@type") == ENTITY_TYPE):
            return m.group(0)
        body = json.dumps(entity_expected(data, slug), ensure_ascii=False, indent=2)
        return "%s\n%s\n%s" % (m.group(1), body, m.group(3))
    return LD_RE.sub(fill, s)


def _dig(o, dotted):
    for k in dotted.split("."):
        o = o.get(k) if isinstance(o, dict) else None
    return o


def atom_value(business, key):
    """Text údaje data-site="key" z objektu business."""
    if key == "address":
        a = business.get("address") or {}
        parts = [a.get("streetAddress"), " ".join(x for x in (a.get("postalCode"), a.get("addressLocality")) if x)]
        v = ", ".join(x for x in parts if x)
    else:
        v = _dig(business, key)
    if not isinstance(v, str) or not v:
        raise BuildError("data-site=\"%s\": v %s.%s není text" % (key, SITE_FILE, ENTITY_KEY))
    if key == "telephone":
        v = format_phone(v)
    return v


def format_phone(v):
    """+420123456789 → +420 123 456 789 (číslo v datech je bez mezer)."""
    m = re.fullmatch(r"(\+\d{3})(\d{3})(\d{3})(\d{3})", v)
    return " ".join(m.groups()) if m else v


def atom_href(business, key):
    if key == "telephone":
        return "tel:" + business["telephone"]
    if key == "email":
        return "mailto:" + business["email"]
    return None


def atom_spans(s):
    return [m.span() for m in ATOM_RE.finditer(s)]


def render_atoms(s, slug, data):
    business = entity_expected(data, slug)

    def fill(m):
        tag, pre, key, post, _ = m.groups()
        attrs = pre + ' data-site="%s"' % key + post
        href = atom_href(business, key) if tag == "a" else None
        if href:
            if not HREF_RE.search(attrs):
                raise BuildError('odkaz s data-site="%s" nemá href' % key)
            attrs = HREF_RE.sub(lambda _: ' href="%s"' % html.escape(href), attrs, count=1)
        return "<%s%s>%s</%s>" % (tag, attrs, html.escape(atom_value(business, key), quote=False), tag)
    return ATOM_RE.sub(fill, s)


STEPS = (("partials", render_partials), ("entity", render_entity), ("atoms", render_atoms))
STEP_LABEL = {"partials": "hlavička a patička (content/partials/)",
              "entity": "údaje o firmě v JSON-LD (content/site.json)",
              "atoms": "údaje z content/site.json v textu (data-site)"}


def render(slug, s, data=None):
    """Stránka slug po všech krocích buildu (v paměti, nic nezapisuje). Odmítnutí = BuildError."""
    data = data or Data()
    for name, step in STEPS:
        try:
            s = step(s, slug, data)
        except BuildError as e:
            raise BuildError("%s.html: %s" % (slug, e)) from None
    return s


# ── kontroly ──
def plain(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def head_of(s):
    end = s.find("</head>")
    return s[:end] if end != -1 else s


def metas(s):
    return {k: html.unescape(v) for _, k, v in META_RE.findall(head_of(s))}


def title_of(s):
    t = TITLE_RE.findall(head_of(s))
    return html.unescape(t[0]).strip() if len(t) == 1 else None


def page_url(slug, data):
    return data.url + ("/" if slug == "index" else "/" + slug)


def check(slug, s, data=None, others=None):
    """[problém] stránky slug (obsah s). others = {slug: obsah} ostatních stránek pro jedinečnost
    title a description (výchozí ze www/)."""
    data = data or Data()
    b = slug + ".html"
    fails = []
    if "\r" in s:
        fails.append("%s: konce řádků CRLF, stránky jsou v LF" % b)
    if "�" in s:
        fails.append("%s: obsahuje znak U+FFFD (rozbité kódování)" % b)
    try:
        manifest = data.manifest()
        site = data.site()
    except (BuildError, OSError) as e:
        return fails + ["%s: %s" % (b, e)]
    kind = (manifest.get(slug) or {}).get("kind")
    if kind is None:
        fails.append("%s: stránka není v %s" % (b, data.manifest_rel))
    # stránka odpovídá datům: render ji nezmění
    cur = s
    for name, step in STEPS:
        try:
            nxt = step(cur, slug, data)
        except BuildError as e:
            fails.append("%s: %s" % (b, e))
            break
        if nxt != cur:
            fails.append("%s: %s neodpovídá datům (spusť python build/build.py)" % (b, STEP_LABEL[name]))
        cur = nxt
    if kind in KINDS:
        try:
            have = {n for _, _, n in slots(s)}
        except BuildError:
            have = set(PARTIALS)                  # chybu značek už hlásí krok partials
        for n in PARTIALS:
            if n not in have:
                fails.append("%s: chybí slot <!-- PARTIAL:%s --> (hlavička a patička z content/partials/)" % (b, n))
    fails += seo(slug, s, metas(s), data)
    fails += links(slug, s, data, manifest, site)
    fails += unique(slug, s, data, manifest, others)
    return fails


def seo(slug, s, meta, data):
    b = slug + ".html"
    fails = []
    if not LANG_RE.search(s):
        fails.append('%s: chybí <html lang="cs">' % b)
    title = title_of(s)
    if not title:
        fails.append("%s: chybí title (právě jeden <title> v hlavičce)" % b)
    elif len(title) > TITLE_MAX:
        fails.append("%s: title má %d znaků, nejvýš %d" % (b, len(title), TITLE_MAX))
    desc = meta.get("description", "").strip()
    if not desc:
        fails.append("%s: chybí meta description" % b)
    elif len(desc) > DESCRIPTION_MAX:
        fails.append("%s: meta description má %d znaků, nejvýš %d" % (b, len(desc), DESCRIPTION_MAX))
    h1 = len(H1_RE.findall(s))
    if h1 != 1:
        fails.append("%s: má %d× h1, čekáno 1" % (b, h1))
    noindex = "noindex" in meta.get("robots", "")
    if not noindex:
        canon = CANONICAL_RE.findall(head_of(s))
        if canon != [page_url(slug, data)]:
            fails.append("%s: canonical má být %s (je %s)" % (b, page_url(slug, data), ", ".join(canon) or "žádný"))
        if meta.get("og:url") != page_url(slug, data):
            fails.append("%s: og:url má být %s" % (b, page_url(slug, data)))
        for k in ("og:title", "og:description"):
            if not meta.get(k, "").strip():
                fails.append("%s: chybí %s" % (b, k))
    blocks = [m.group(2) for m in LD_RE.finditer(s)]
    faq = None
    for i, body in enumerate(blocks):
        o = _ld(body)
        if o is None:
            fails.append("%s: JSON-LD #%d neparsuje" % (b, i + 1))
        elif isinstance(o, dict) and o.get("@type") == "FAQPage":
            faq = len(o.get("mainEntity") or [])
    visible = s.count(FAQ_ITEM)
    if (faq or 0) != visible:
        fails.append("%s: FAQPage v JSON-LD má %d otázek, na stránce je jich %d" % (b, faq or 0, visible))
    for img in IMG_RE.findall(s):
        for a in ("alt", "width", "height"):
            if not re.search(r"\s%s=\"" % a, img):
                fails.append("%s: obrázek bez %s: %s" % (b, a, img[:80]))
    return fails


def links(slug, s, data, manifest, site):
    """Interní odkazy vedou na stránky z manifestu nebo soubory webu, kontakty sedí na data."""
    b = slug + ".html"
    fails = []
    business = site[ENTITY_KEY]
    for url in sorted(set(HREF_ALL_RE.findall(s))):
        if url.startswith("tel:"):
            if url != "tel:" + business.get("telephone", ""):
                fails.append("%s: odkaz %s nesedí na telefon v %s" % (b, url, SITE_FILE))
        elif url.startswith("mailto:"):
            if url[7:].split("?")[0] != business.get("email"):
                fails.append("%s: odkaz %s nesedí na e-mail v %s" % (b, url, SITE_FILE))
        elif url.startswith("/") and not url.startswith("//"):
            path = url.split("#")[0].split("?")[0]
            if path == "/":
                continue
            target = path[1:]
            if target in manifest:
                continue
            if not (data.root / data.source / target).is_file():
                fails.append("%s: odkaz %s nevede na stránku ani soubor webu" % (b, url))
    return fails


def unique(slug, s, data, manifest, others):
    """title a description se mezi stránkami neopakují."""
    b = slug + ".html"
    fails = []
    mine = (title_of(s), metas(s).get("description"))
    for other in sorted(manifest):
        if other == slug:
            continue
        if others is not None and other in others:
            o = others[other]
        else:
            try:
                o = data.page(other)
            except FileNotFoundError:
                continue
        if mine[0] and title_of(o) == mine[0]:
            fails.append("%s: stejný title jako %s.html" % (b, other))
        if mine[1] and metas(o).get("description") == mine[1]:
            fails.append("%s: stejná meta description jako %s.html" % (b, other))
    return fails


def verify(data=None, pages=None):
    """[problém] celého webu: manifest a soubory stránek, šablony, každá stránka. pages = {slug: obsah}
    (výchozí ze www/)."""
    data = data or Data()
    fails = []
    try:
        manifest = data.manifest()
        data.site()
    except (BuildError, OSError) as e:
        return [str(e)]
    for name in PARTIALS:
        try:
            data.template(name)
        except BuildError as e:
            fails.append(str(e))
    files = {p.stem for p in (data.root / data.source).glob("*.html")}
    src, man = data.source, data.manifest_rel
    for slug in sorted(files - set(manifest)):
        fails.append("%s/%s.html: stránka není v %s" % (src, slug, man))
    for slug, it in sorted(manifest.items()):
        if it.get("output") != "%s/%s.html" % (src, slug) or it.get("kind") not in KINDS:
            fails.append("%s: položka %s má mít output %s/%s.html a kind %s"
                         % (man, slug, src, slug, " nebo ".join(sorted(KINDS))))
    if pages is None:
        pages = {slug: data.page(slug) for slug in sorted(manifest) if slug in files}
    for slug in sorted(set(manifest) - set(pages)):
        fails.append("%s/%s.html: stránka z %s chybí" % (src, slug, man))
    for slug, s in sorted(pages.items()):
        fails += check(slug, s, data, pages)
    return fails


# ── typografie (editor ji dává textu, kterého se úprava dotkla) ──
_TAG = re.compile(r"(<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>)")
_ONE_LETTER = re.compile(r"(^|[\s(„]|&nbsp;)([vVsSzZkKaAiIoOuU]) ")
_UNIT = re.compile(r"(?<=\d) (?=(?:%|km|m|cm|mm|kg|h|Kč)(?![^\W\d_]))")


def typo_text(s):
    s = _UNIT.sub("&nbsp;", s)
    prev = None
    while prev != s:                      # „a v dílně“: obě jednopísmenná slova
        prev = s
        s = _ONE_LETTER.sub(lambda m: m.group(1) + m.group(2) + "&nbsp;", s)
    return s


def typo_html(s):
    """Pevné mezery za jednopísmennými předložkami a spojkami a mezi číslem a jednotkou, jen v textu
    mimo tagy. Idempotentní."""
    parts = _TAG.split(s)
    return "".join(p if i % 2 else typo_text(p) for i, p in enumerate(parts))
