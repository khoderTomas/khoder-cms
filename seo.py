# -*- coding: utf-8 -*-
"""Panel SEO editoru: hlavička stránky, texty JSON-LD, údaje o autorovi webu a FAQ v JSON-LD.

Čtení: title, description, og/twitter a keywords z hlavičky (robots jen ke čtení), texty JSON-LD
podle @type a klíče (allowlist adaptéru, Rules.ld_allow; co plní build nebo panel nenabízí, je
zamčené s důvodem, Rules.ld_fixed) a otázky s odpověďmi z FAQPage, spárované podle pořadí
s viditelným FAQ (<details class="faq-item">). Odpovědi v JSON-LD jsou psané zvlášť.

Zápis ruční a owner stránky (apply): mění se jen zakódovaná hodnota, tedy text v <title>,
atribut content u meta nebo hodnota v JSON-LD (jsonsource.replace: řetězec jako
json.dumps(ensure_ascii=False), seznam ve stylu původního seznamu), a zpětné parsování musí
ukázat změnu jen v cílové hodnotě.

Generovaná stránka: title, description, keywords, texty JSON-LD a FAQ v JSON-LD jsou literály
skriptu stránky. Stejný text mívá i viditelné FAQ a rychlá odpověď, proto se literál hledá
značkou: ke každému literálu skriptu, jehož hodnota se rovná poli (u seznamu položce), se připojí
značka a zkušební build skriptu ukáže, ve kterém poli skončila (cache podle otisku jako
proveniance bloků). Seznam jde upravit, když jsou jeho položky po sobě jdoucí literály jednoho
seznamu ve skriptu; zapíše se celý seznam. og:title a twitter:title generátor odvozuje z title
(adapter.RULES.og_title), og a twitter description z description, takže je panel ukazuje jako
jedno pole.

Údaje o autorovi webu (adapter.RULES.entity, třeba Person v souboru dat webu) nejsou
texty stránky: společné hodnoty platí pro všechny stránky s blokem, přepis po stránkách nese
soubor dat (pages); stránky z nich sestavuje build. entity_panel a entity_apply pracují nad
daty souboru, zápis a přestavění stránek řídí apply.Editor.

Hodnoty jsou vždy prostý text (H.plain: bez tagů, NBSP a WJ, rovné uvozovky
na „…”), jiný by kontrola stránky (adapter.Site.check) nemusela pustit.
"""
import collections
import copy
import html
import json
import re

import adapter as AD
import blocks as B
import htmltext as H
import jsonsource as J
import provenance as PV
import pysource as P

HEAD = [  # (klíč, popisek, doporučená délka)
    ("title", "Title", 60),
    ("description", "Meta description", 155),
    ("og:title", "og:title", 60),
    ("og:description", "og:description", 155),
    ("twitter:title", "twitter:title", 60),
    ("twitter:description", "twitter:description", 155),
]
LABEL = {k: lab for k, lab, _ in HEAD}
KW, ROBOTS = "keywords", "robots"
LABEL.update({KW: "Klíčová slova"})
GENERATED = ("title", "description")        # zbytek hlavičky generátor odvozuje
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_META = re.compile(r'<meta (?:name|property)="(description|keywords|og:title|og:description|twitter:title|twitter:description)"'
                   r' content="([^"]*)"')
_ROBOTS = re.compile(r'<meta name="robots" content="([^"]*)"')
LD_RE = re.compile(r'(<script type="application/ld\+json">)(.*?)(</script>)', re.S)
_FAQ_ID = re.compile(r"^faq:(\d+):([qa])$")
_LD_ID = re.compile(r"^ld:(\d+):(.+)$")
M0, M1 = chr(0xE000), chr(0xE001)
_MARK = re.compile(M0 + "([0-9a-z]+)" + M1)

# klíče JSON-LD bez textu pro člověka (adresy, kontakty, čísla, data, kódy)
LD_SKIP = {"url", "item", "image", "logo", "sameAs", "email", "telephone", "price", "priceCurrency",
           "priceValidUntil", "latitude", "longitude", "ratingValue", "bestRating", "worstRating",
           "reviewCount", "ratingCount", "datePublished", "dateModified", "uploadDate", "inLanguage",
           "mainEntityOfPage", "addressCountry", "postalCode", "taxID", "unitCode", "contentUrl",
           "embedUrl", "thumbnailUrl", "duration", "availability"}
_WORD = re.compile(r"[^\W\d_]{2,}")
_URLISH = re.compile(r"^(?:https?:|mailto:|tel:|/|#|assets/|data:)")
_DATE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:T[\d:+.Z-]*)?$")

LOCK_COMPOSED = "Text vzniká ve skriptu skládáním, editor ho ve zdroji nenašel. Upravit přes Clauda."
LOCK_GEN_SHARED = "Stejný text mají všechny generované stránky (sdílený soubor generátoru), mění ho Claude Code."
LOCK_LIST = "Seznam ve skriptu stránky není zapsaný jako seznam textů, mění ho Claude Code."
WHY_NOT_ALLOWED = "Panel ho nenabízí (%s není mezi texty, které jde v adminu měnit)."
WHY_CRUMB = "Drobečková navigace: název odkazované stránky, mění se s ní."
WHY_LINKED = "Mění se s textem bloku na stránce."
WHY_ITEMLIST = "Název odkazované stránky, mění se na ní."
WHY_FAQ_UNKNOWN = "Neznámý tvar položky v JSON-LD."


class SeoError(Exception):
    pass


# ── prostý text a kódování ──
def clean(s):
    """Hodnota pole, jak se zapíše: prostý text podle P02."""
    return H.plain(s or "")


def clean_list(items):
    """Seznam, jak se zapíše: položky jako prostý text, bez prázdných a bez opakování."""
    out = []
    for x in items or []:
        v = clean(x)
        if v and v not in out:
            out.append(v)
    return out


def html_plain(fragment):
    """Viditelný text fragmentu stránky jako prostý text (<br> a konce odstavců jako mezera)."""
    return H.plain(re.sub(r"<br\s*/?>|</(?:p|li|div|h\d)>", " ", fragment))


def enc_html(v):
    """Text do <title> i do atributu content (generátor dává literál do obou)."""
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def og_title(title):
    """og:title a twitter:title generované stránky, jak je generátor odvozuje z title."""
    return AD.RULES.og_title(title)


def kw_split(raw):
    """Položky meta keywords (dekódovaný obsah atributu)."""
    return [x.strip() for x in raw.split(",") if x.strip()]


def kw_join(items):
    return ", ".join(items)


def is_text(v):
    """Je hodnota JSON-LD text pro člověka (seznam: aspoň jedna položka)?"""
    if isinstance(v, list):
        return any(is_text(x) for x in v)
    return (isinstance(v, str) and bool(_WORD.search(v)) and not _URLISH.match(v) and not _DATE.match(v)
            and not P.IDENT.match(v))


# ── čtení stránky ──
def head_spans(src):
    """{klíč: (start, konec)} surových hodnot v hlavičce. Pole, které je tam víckrát, chybí."""
    end = src.find("</head>")
    head = src[:end if end != -1 else len(src)]
    found = collections.defaultdict(list)
    for m in _TITLE.finditer(head):
        found["title"].append(m.span(1))
    for m in _META.finditer(head):
        found[m.group(1)].append(m.span(2))
    return {k: v[0] for k, v in found.items() if len(v) == 1}


def robots(src):
    end = src.find("</head>")
    m = _ROBOTS.search(src[:end if end != -1 else len(src)])
    return m.group(1) if m else None


def _objects(tree):
    """(cesta, objekt) nejvyšší úrovně bloku JSON-LD (objekt, seznam i @graph)."""
    if isinstance(tree, dict):
        if isinstance(tree.get("@graph"), list):
            for i, o in enumerate(tree["@graph"]):
                yield ("@graph", i), o
        else:
            yield (), tree
    elif isinstance(tree, list):
        for i, o in enumerate(tree):
            yield (i,), o


def ld_walk(v, path=(), types=(), ctx=()):
    """(cesta, klíč, hodnota, @type předků, klíče předků) hodnot objektu JSON-LD, které jsou řetězec
    nebo seznam řetězců (seznam je jedna hodnota)."""
    if isinstance(v, dict):
        t = v.get("@type")
        types = types + ((t,) if isinstance(t, str) else ())
        for k, x in v.items():
            if k.startswith("@"):
                continue
            if isinstance(x, str) or (isinstance(x, list) and x and all(isinstance(i, str) for i in x)):
                yield path + (k,), k, x, types, ctx
            else:
                yield from ld_walk(x, path + (k,), types, ctx + (k,))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from ld_walk(x, path + (i,), types, ctx)


def ld_id(bi, path):
    return "ld:%d:%s" % (bi, "/".join(str(p) for p in path))


def ld_parse(fid):
    """(index bloku, cesta) z id pole JSON-LD, nebo None."""
    m = _LD_ID.match(fid)
    if not m:
        return None
    return int(m.group(1)), tuple(int(p) if p.isdigit() else p for p in m.group(2).split("/"))


def ld_meta(src):
    """{(index bloku, cesta): (hodnota, @type předků, klíč, klíče předků)} textů JSON-LD mimo FAQPage
    (to jsou pole faq:…), seznam řetězců jako jedna hodnota. Cesta je od kořene bloku."""
    out = {}
    for bi, m in enumerate(LD_RE.finditer(src)):
        try:
            tree = json.loads(m.group(2))
        except ValueError:
            continue
        for prefix, obj in _objects(tree):
            if not isinstance(obj, dict) or obj.get("@type") == "FAQPage":
                continue
            for path, key, val, types, ctx in ld_walk(obj):
                if key not in LD_SKIP and is_text(val):
                    out[(bi, prefix + path)] = (val, types, key, ctx)
    return out


def ld_texts(src):
    """{(index bloku, cesta): hodnota} textů JSON-LD (ld_meta)."""
    return {k: v[0] for k, v in ld_meta(src).items()}


def faq_ld(src):
    """[(index bloku JSON-LD, cesta k otázce, otázka, odpověď)] z FAQPage.
    Otázka jiného tvaru (odpověď jako seznam…) má místo textu None."""
    for bi, m in enumerate(LD_RE.finditer(src)):
        try:
            tree = json.loads(m.group(2))
        except ValueError:
            continue
        for prefix, obj in _objects(tree):
            if not (isinstance(obj, dict) and obj.get("@type") == "FAQPage"):
                continue
            out = []
            for i, q in enumerate(obj.get("mainEntity") or []):
                a = q.get("acceptedAnswer") if isinstance(q, dict) else None
                name = q.get("name") if isinstance(q, dict) else None
                text = a.get("text") if isinstance(a, dict) else None
                out.append((bi, prefix + ("mainEntity", i),
                            name if isinstance(name, str) else None, text if isinstance(text, str) else None))
            return out
    return []


def faq_visible(src):
    """[(otázka, odpověď)] viditelného FAQ jako prostý text, v pořadí stránky."""
    out = []
    for n in B._iter(B.parse(src)):
        if n.tag != "details" or "faq-item" not in n.cls().split():
            continue
        summ = next((c for c in n.children if c.tag == "summary"), None)
        body = next((c for c in n.children if "faq-body" in c.cls().split()), None)
        if summ is None or body is None or not (summ.closed and body.closed):
            continue
        q = src[summ.se:summ.ie]
        for c in summ.children:                      # ikona rozbalení není součást otázky
            if B.is_icon(c):
                q = q.replace(src[c.s:c.e], " ", 1)
        out.append((html_plain(q), html_plain(src[body.se:body.ie])))
    return out


def ld_values(src):
    """{id pole: hodnota} textů JSON-LD (ld:…) a keywords jako seznam, jak je ukazuje panel."""
    out = {ld_id(bi, path): v for (bi, path), v in ld_texts(src).items()}
    spans = head_spans(src)
    if KW in spans:
        a, z = spans[KW]
        out[KW] = kw_split(html.unescape(src[a:z]))
    return out


def values(src):
    """{id pole: aktuální hodnota jako text} — hlavička (dekódovaná), texty JSON-LD (ld:…, seznam
    spojený čárkou) a FAQ v JSON-LD (faq:<i>:q|a). Podle nich panel Změny ukazuje rozdíl."""
    out = {}
    for k, (a, z) in head_spans(src).items():
        out[k] = html.unescape(src[a:z])
    for (bi, path), v in ld_texts(src).items():
        out[ld_id(bi, path)] = ", ".join(v) if isinstance(v, list) else v
    for i, (_, _, q, a) in enumerate(faq_ld(src)):
        if q is not None:
            out["faq:%d:q" % i] = q
        if a is not None:
            out["faq:%d:a" % i] = a
    return out


def _probe_values(src):
    """{id: surová hodnota} polí, která tvoří literály skriptu generované stránky: hlavička surově
    (tak ji generátor skládá), položky seznamů JSON-LD zvlášť (id seznamu + /index), FAQ."""
    out = {}
    for k, (a, z) in head_spans(src).items():
        if k in GENERATED or k == KW:
            out[k] = src[a:z]
    for (bi, path), v in ld_texts(src).items():
        if isinstance(v, list):
            out.update({ld_id(bi, path + (i,)): x for i, x in enumerate(v)})
        else:
            out[ld_id(bi, path)] = v
    for i, (_, _, q, a) in enumerate(faq_ld(src)):
        if q is not None:
            out["faq:%d:q" % i] = q
        if a is not None:
            out["faq:%d:a" % i] = a
    return out


# ── zápis do stránky ──
def _get(tree, path):
    for p in path:
        tree = tree[p]
    return tree


def ld_set(src, bi, path, new):
    """Stránka s novou hodnotou (řetězec nebo seznam řetězců) v bloku JSON-LD bi na cestě path.
    Mění se jen zakódovaná hodnota (jsonsource.replace), zpětné parsování musí dát strom, kde se
    liší jen tahle hodnota."""
    blocks = list(LD_RE.finditer(src))
    if bi >= len(blocks):
        raise SeoError("Blok JSON-LD na stránce není, nic jsem nezapsal.")
    m = blocks[bi]
    try:
        raw = J.replace(m.group(2), path, new)
    except (J.JsonSourceError, ValueError):
        raise SeoError("Hodnotu v JSON-LD se nepodařilo bezpečně najít, nic jsem nezapsal.") from None
    return src[:m.start(2)] + raw + src[m.end(2):]


def apply(src, changes, derived=False):
    """Stránka s novými hodnotami polí {id: prostý text, u keywords a seznamů JSON-LD seznam}.
    derived = generovaná stránka: og a twitter dostanou title a description tak, jak je odvodí
    generátor."""
    ch = dict(changes)
    spans = head_spans(src)
    if derived:
        if "title" in ch:
            ch.update({k: og_title(ch["title"]) for k in ("og:title", "twitter:title") if k in spans})
        if "description" in ch:
            ch.update({k: ch["description"] for k in ("og:description", "twitter:description") if k in spans})
    head = sorted((spans[k], kw_join(v) if k == KW else v) for k, v in ch.items() if k in LABEL and k in spans)
    for (a, z), v in reversed(head):
        src = src[:a] + enc_html(v) + src[z:]
    for k, v in ch.items():
        p = ld_parse(k)
        if p:
            src = ld_set(src, p[0], p[1], v)
    faq = faq_ld(src)
    for k, v in ch.items():
        m = _FAQ_ID.match(k)
        if m:
            bi, path, _, _ = faq[int(m.group(1))]
            src = ld_set(src, bi, path + (("name",) if m.group(2) == "q" else ("acceptedAnswer", "text")), v)
    return src


# ── generované stránky: který literál tvoří které pole ──
class SeoProv:
    __slots__ = ("key", "script", "targets", "lists", "shared", "error")

    def __init__(self, key, script):
        self.key, self.script = key, script
        self.targets = {}              # id pole (u seznamu položky) → index literálu ve skriptu stránky
        self.lists = {}                # id seznamu JSON-LD → (začátek, konec, oddělovač) seznamu ve skriptu
        self.shared = frozenset()      # hodnoty literálů sdílených souborů generátoru
        self.error = None


def _list_span(text, lits):
    """(začátek, konec, oddělovač) seznamu ve skriptu, jehož položky jsou přesně literály lits
    v tomto pořadí (obyčejné řetězce), jinak None."""
    if not lits or any(x.fstr or x.raw for x in lits):
        return None
    starts = [x.a - len(x.q) for x in lits]
    ends = [x.z + len(x.q) for x in lits]
    if any(text[s - 1:s].isalpha() for s in starts):     # předpona řetězce (b, u…)
        return None
    pre = re.search(r"\[\s*$", text[:starts[0]])
    post = re.match(r"\s*,?\s*\]", text[ends[-1]:])
    if not pre or not post:
        return None
    seps = [text[e:s] for e, s in zip(ends, starts[1:])]
    if not all(re.fullmatch(r"\s*,\s*", s) for s in seps):
        return None
    return pre.start(), ends[-1] + post.end(), seps[0] if seps else ","


def probe(site, slug, src):
    """Literály skriptu stránky pro pole hlavičky, keywords, texty JSON-LD a FAQ v JSON-LD
    (jeden zkušební build skriptu)."""
    srcs = PV.sources(site, slug)
    fk = site.registry()[slug]["source"]
    sp = SeoProv(PV.cache_key(src, srcs), fk)
    sp.shared = frozenset(lit.value for rel in site.shared_files() if rel in srcs for lit in P.literals(srcs[rel]))
    text = srcs[fk]
    lits = P.literals(text)
    want = _probe_values(src)
    cands = collections.defaultdict(list)            # id pole → [index literálu]
    for fid, val in want.items():
        for li, lit in enumerate(lits):
            if val and lit.value == val:
                cands[fid].append(li)
    marked = {li for ls in cands.values() for li in ls}
    s = text
    for li in sorted(marked, key=lambda i: -lits[i].a):
        try:
            s = P.replace(s, lits[li], lits[li].value + M0 + format(li, "x") + M1)
        except ValueError:
            continue
    ok, err, outputs = site.generate({fk: s}, [fk])
    out = outputs.get(slug) if ok else None
    if out is None:
        sp.error = err or "zkušební build stránku nevytvořil"
        return sp
    got = _probe_values(out)
    for fid, val in want.items():
        v = got.get(fid, "")
        hits = [int(x, 16) for x in _MARK.findall(v)]
        if len(hits) == 1 and hits[0] in cands[fid] and v == val + M0 + format(hits[0], "x") + M1:
            sp.targets[fid] = hits[0]
    for (bi, path), v in ld_texts(src).items():
        if isinstance(v, list):
            ids = [ld_id(bi, path + (i,)) for i in range(len(v))]
            if all(i in sp.targets for i in ids) and len({sp.targets[i] for i in ids}) == len(ids):
                span = _list_span(text, [lits[sp.targets[i]] for i in ids])
                if span:
                    sp.lists[ld_id(bi, path)] = span
    return sp


# ── panel ──
def faq_lock(src, fid):
    """Důvod zámku pole faq:<i>:q|a, které build převzal z jiné stránky
    (adapter.RULES.faq_from: převzaté otázky jsou ve FAQPage za vlastními)."""
    m, fm = _FAQ_ID.match(fid), next(iter(AD.RULES.faq_from(src)), None)
    if not m or not fm:
        return None
    a, z, slug = fm
    own = (src[:a] + src[z:]).count('<details class="faq-item"')
    return B.LOCK_FAQ_FROM % slug if int(m.group(1)) >= own else None


def _row(fid, label, value, state, why, **kw):
    return dict({"id": fid, "label": label, "value": value, "list": isinstance(value, list), "state": state,
                 "edit": state == "edit", "why": why}, **kw)


def _gen_state(sp, fid, value):
    """Stav pole generované stránky: literál skriptu, společný text generátoru, nebo složený."""
    if isinstance(value, list):
        if fid in sp.lists:
            return "edit", None
        if all(fid + "/%d" % i in sp.targets for i in range(len(value))):
            return "locked", LOCK_LIST
    elif fid in sp.targets:
        return "edit", None
    vals = value if isinstance(value, list) else [value]
    if all(v in sp.shared for v in vals):
        return "locked", LOCK_GEN_SHARED
    return "locked", LOCK_COMPOSED


def ld_panel(src, kind, sp=None, linked=frozenset(), lock=None):
    """Texty JSON-LD po objektech: [{bi, type, label, rows}]. Mimo FAQPage (pole faq:…) a mimo
    objekt údajů o autorovi (RULES.entity, oddíl O vás). linked = texty bloků stránky (prostý
    text): poslední položka drobečkové navigace se s nimi mění sama."""
    rules = AD.RULES
    ent = (rules.entity or {}).get("type")
    gen = kind == "generated"
    out = []
    for bi, m in enumerate(LD_RE.finditer(src)):
        try:
            tree = json.loads(m.group(2))
        except ValueError:
            continue
        for prefix, obj in _objects(tree):
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            if t == "FAQPage" or (ent and t == ent):
                continue
            rows = []
            crumbs = obj.get("itemListElement") if t == "BreadcrumbList" else None
            for path, key, val, types, ctx in ld_walk(obj):
                if key in LD_SKIP or not is_text(val):
                    continue
                ty = types[-1] if types else t
                fid = ld_id(bi, prefix + path)
                where = "%s.%s" % (ty, key)
                label = rules.ld_label(ty, key, ctx)
                fixed = rules.ld_fixed(ty, key, ctx, val, gen)
                if rules.quote_types & set(types):
                    st, why, label = "locked", rules.quote_why, "Citace recenze"
                elif fixed:
                    st, why, label = fixed[0], fixed[1], fixed[2] or label or key
                elif t == "BreadcrumbList" and crumbs:
                    last = path[:2] == ("itemListElement", len(crumbs) - 1)
                    label = "Poslední položka" if last else "Položka %d" % (path[1] + 1)
                    st, why = ("computed", WHY_LINKED) if last and H.norm(val) in linked else ("locked", WHY_CRUMB)
                elif t == "ItemList" and len(path) > 1 and isinstance(path[1], int):
                    st, why, label = "locked", WHY_ITEMLIST, "Položka %d" % (path[1] + 1)
                elif label is None:
                    st, why, label = "locked", WHY_NOT_ALLOWED % where, key
                elif lock:
                    st, why = "locked", lock
                elif gen:
                    st, why = _gen_state(sp, fid, val)
                else:
                    st, why = "edit", None
                rows.append(_row(fid, label, val, st, why, key=where,
                                 path=" › ".join(str(p) for p in path)))
            if rows:
                out.append({"bi": bi, "type": t, "label": rules.ld_types.get(t, t), "rows": rows})
    return out


def panel(src, kind, sp=None, linked=frozenset(), lock=None):
    """Data pro panel SEO. sp = proveniance generované stránky (SeoProv), lock = zámek stránky
    (owner stránka, selhaný zkušební build)."""
    cur = values(src)
    gen = kind == "generated"

    def field(fid, value=None):
        why = faq_lock(src, fid)
        shown = cur[fid] if value is None else value
        if not why and not lock and gen:
            st, why = _gen_state(sp, fid, cur[fid])       # keywords: jeden literál se seznamem
            return {"value": shown, "state": st, "edit": st == "edit", "why": why}
        st = "locked" if why or lock else "edit"
        return {"value": shown, "state": st, "edit": st == "edit", "why": why or lock}

    fields = []
    for k, label, mx in HEAD:
        if k in cur and (not gen or k in GENERATED):
            fields.append(dict(field(k), id=k, label=label, max=mx))
    if KW in cur:
        fields.append(dict(field(KW, kw_split(cur[KW])), id=KW, label=LABEL[KW], list=True, sep=", "))
    rb = robots(src)
    if rb is not None:
        fields.append({"id": ROBOTS, "label": "Indexování (robots)", "value": rb, "state": "ro", "edit": False,
                       "why": AD.RULES.robots_why(gen)})
    vis = faq_visible(src)
    faq = []
    for i, (_, _, q, a) in enumerate(faq_ld(src)):
        item = {"i": i, "vis_q": vis[i][0] if i < len(vis) else None, "vis_a": vis[i][1] if i < len(vis) else None}
        for part, v in (("q", q), ("a", a)):
            fid = "faq:%d:%s" % (i, part)
            item[part] = dict(field(fid), id=fid) if v is not None else {
                "id": fid, "value": "", "state": "locked", "edit": False, "why": WHY_FAQ_UNKNOWN}
        faq.append(item)
    note = None
    if faq and len(vis) != len(faq):
        note = "Viditelné FAQ má %d otázek, JSON-LD %d. Páruji podle pořadí." % (len(vis), len(faq))
    return {"fields": fields, "ld": ld_panel(src, kind, sp, linked, lock), "faq": faq, "faq_note": note,
            "derived": gen}


def label(fid, src=None):
    """Popisek pole (journal, panel Změny); u textu JSON-LD podle stránky src."""
    m = _FAQ_ID.match(fid)
    if m:
        return "FAQ %d %s" % (int(m.group(1)) + 1, "otázka" if m.group(2) == "q" else "odpověď")
    p = ld_parse(fid)
    if p:
        meta = ld_meta(src).get(p) if src else None
        if meta:
            _, types, key, ctx = meta
            ty = types[-1] if types else "?"
            ent = (AD.RULES.entity or {}).get("type")
            if ent and ent in types:
                return "O vás · %s" % entity_label(key)
            return AD.RULES.ld_label(ty, key, ctx) or "%s.%s" % (ty, key)
        keys = [x for x in p[1] if isinstance(x, str)]
        return "JSON-LD %s" % (keys[-1] if keys else fid)
    if fid.startswith("p:"):
        return "O vás · %s" % entity_label(fid[2:])
    if fid == "knows":
        return "O vás · %s" % entity_label((AD.RULES.entity or {}).get("topics", "témata"))
    return LABEL.get(fid, fid)


# ── údaje o autorovi webu (RULES.entity): soubor dat {<key>: objekt, pages: {slug: přepis}} ──
def _dig(o, dotted):
    for k in dotted.split("."):
        o = o.get(k) if isinstance(o, dict) else None
    return o


def entity_label(dotted):
    ent = AD.RULES.entity or {}
    for k, lab in list(ent.get("text", ())) + list(ent.get("contacts", ())):
        if k == dotted:
            return lab
    return "Témata" if dotted == ent.get("topics") else dotted


def entity_expected(data, slug):
    ent = AD.RULES.entity
    return dict(data[ent["key"]], **(data.get("pages") or {}).get(slug, {}))


def entity_panel(data, slug, carriers, page_obj, lock=None, shared_lock=None):
    """Oddíl O vás: společné údaje a přepis stránky z dat entity. carriers = stránky s blokem entity,
    page_obj = blok na stránce (None = stránka ho nenese), lock = zámek stránky (přepis stránky),
    shared_lock = proč nejde měnit společné údaje (role). Vrací None, když stránka blok nenese."""
    ent = AD.RULES.entity
    if not ent or page_obj is None:
        return None
    own = (data.get("pages") or {}).get(slug, {})
    exp = entity_expected(data, slug)
    drift = None
    if page_obj != exp:
        drift = "Údaje o vás na stránce neodpovídají %s. Spusť build (Claude Code)." % ent["file"]
    fields = []
    for k, lab in ent.get("text", ()):
        v = _dig(exp, k)
        if v is None:
            continue
        scope = "page" if k.split(".")[0] in own else "site"
        why = drift or (lock if scope == "page" else shared_lock)
        fields.append(_row("p:" + k, lab, v, "locked" if why else ("edit" if scope == "page" else "shared"), why,
                           key="%s.%s" % (ent["type"], k), scope=scope))
    contacts = []
    for k, lab in ent.get("contacts", ()):
        v = _dig(exp, k)
        if v is not None:
            contacts.append({"label": lab, "value": v if isinstance(v, list) else [v]})
    tk = ent.get("topics")
    topics = None
    if tk and isinstance(exp.get(tk), list):
        is_own = tk in own
        dflt = data[ent["key"]].get(tk) or []
        why = drift or (lock if is_own else shared_lock)
        topics = _row("knows", entity_label(tk), exp[tk], "locked" if why else ("edit" if is_own else "shared"), why,
                      key="%s.%s" % (ent["type"], tk), own=is_own, default=dflt,
                      shared_why=shared_lock, page_why=drift or lock)
    pages = (data.get("pages") or {})
    return {"file": ent["file"], "fields": fields, "contacts": contacts, "topics": topics, "drift": drift,
            "pages": sorted(carriers),
            "default_pages": sorted(s for s in carriers if tk not in pages.get(s, {})) if tk else []}


def _put(o, dotted, v):
    ks = dotted.split(".")
    for k in ks[:-1]:
        o = o.setdefault(k, {})
    o[ks[-1]] = v


def entity_apply(data, slug, changes):
    """Nová data entity a co se mění: (data, sdílené změny [id], změny jen stránky [id]).
    changes = [{id: p:<klíč>, old, new}] a {id: knows, old, new, own} (own = vlastní témata stránky).
    old je hodnota, kterou panel ukázal; jinak SeoError(stale)."""
    ent = AD.RULES.entity
    data = copy.deepcopy(data)
    base = data[ent["key"]]
    pages = data.setdefault("pages", {})
    own = pages.setdefault(slug, {})
    exp = entity_expected(data, slug)
    shared, local = [], []
    for c in changes:
        fid = c["id"]
        if fid == "knows":
            tk = ent["topics"]
            new = clean_list(c.get("new"))
            if _dig(exp, tk) != c.get("old"):
                raise SeoError("stale:%s" % label(fid))
            if not new:
                raise SeoError("empty:%s" % label(fid))
            if c.get("own"):
                if own.get(tk) != new:
                    own[tk] = new
                    local.append(fid)
            else:
                if tk in own:
                    own.pop(tk)
                    local.append(fid)
                if base.get(tk) != new:
                    base[tk] = new
                    shared.append(fid)
            continue
        if not fid.startswith("p:"):
            raise SeoError("Pole %s do údajů o vás nepatří." % fid)
        k = fid[2:]
        if k not in dict(ent.get("text", ())):
            raise SeoError("Pole %s se v adminu nemění." % label(fid))
        cur = _dig(exp, k)
        if cur is None:
            raise SeoError("Pole %s na stránce není." % label(fid))
        if cur != c.get("old"):
            raise SeoError("stale:%s" % label(fid))
        new = clean_list(c.get("new")) if isinstance(cur, list) else clean(c.get("new"))
        if not new:
            raise SeoError("empty:%s" % label(fid))
        if new == cur:
            continue
        if k.split(".")[0] in own:
            target, bucket = own, local
        else:
            target, bucket = base, shared
        _put(target, k, new)
        bucket.append(fid)
    if not own:
        pages.pop(slug, None)
    return data, shared, local
