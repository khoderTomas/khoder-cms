# -*- coding: utf-8 -*-
"""Zpětná rekonstrukce zdroje bloku z innerHTML, které vrátil prohlížeč.

Neskládá blok znovu, ale porovnává: originál i verze z prohlížeče se rozloží
na jednotky (znak, úsek bílých znaků, tag, pevný prvek) a SequenceMatcher najde,
co se změnilo. Nezměněné jednotky se zkopírují jako PŮVODNÍ BAJTY zdroje
(entity, surové NBSP, CRLF, atributy), escapuje se jen vložený text. Úprava
beze změny textu tak dá byte-identický výsledek.

Úsek bílých znaků (mezera, NBSP, konec řádku s odsazením) je jedna jednotka se
stejným klíčem: Chrome při editaci mění mezery na NBSP a zpátky a to se nesmí
propsat do zdroje. Pevnou mezeru řídí typografie webu (adapter.RULES.typo), ne prohlížeč.
"""
import collections
import html
import json
import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

import adapter as AD

WS = ("W",)
_WS_CHARS = set(" \t\r\n\f\u00a0")
_TEXT_TOKEN = re.compile(r"<!--.*?-->|&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);|\r\n|.", re.S)
NEW_TAGS = {"strong", "em", "a"}
# Nový odkaz z editoru smí vést jen na stránku webu, kotvu, http(s), e-mail nebo telefon (ADR 0014)
SAFE_SCHEMES = {"http", "https", "mailto", "tel"}
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_CTRL = re.compile(r"[\x00-\x20\x7f]+")
_RENAME = {"b": "strong", "i": "em"}
VOID = {"br", "img", "input", "meta", "link", "hr", "wbr", "source", "area", "base",
        "col", "embed", "param", "track"}


class EditError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def safe_href(href):
    """Smí odkaz z editoru vést tam, kam vede? Prohlížeč bílé a řídicí znaky v adrese zahodí,
    takže „java“ + tabulátor + „script:“ je pořád javascript:."""
    m = _SCHEME.match(_CTRL.sub("", html.unescape(href or "")))
    return m is None or m.group(1).lower() in SAFE_SCHEMES


def ld_enc(value):
    """Řetězec pro JSON-LD uvnitř <script>: json.dumps a „</“ zapsané s escapovaným lomítkem,
    ať text nikdy neukončí skript."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _text_units(raw):
    """Text originálu → [(klíč, raw)]: znak po znaku, entity dekódované."""
    out = []
    for m in _TEXT_TOKEN.finditer(raw):
        t = m.group(0)
        if t.startswith("<!--"):
            out.append((("c", t[4:-3]), t))
        elif t.startswith("&"):
            out.append((html.unescape(t), t))
        elif t == "\r\n":
            out.append(("\n", t))
        else:
            out.append((t, t))
    return out


def _merge_ws(units):
    """Sousední bílé znaky → jedna jednotka WS s raw = spojení originálů."""
    out = []
    for key, raw in units:
        ws = isinstance(key, str) and key != "" and all(c in _WS_CHARS for c in key)
        if ws and out and out[-1][0] == WS:
            out[-1] = (WS, None if raw is None else out[-1][1] + raw)
        elif ws:
            out.append((WS, raw))
        else:
            out.append((key, raw))
    return out


def orig_units(src, block):
    """Jednotky originálního inner bloku z blocks.Block."""
    a, z = block.node.se, block.node.ie
    events = []
    for k, e, atom in block.elems:
        if atom:
            events.append((e.s, e.e, ("a", k)))
        elif e.tag == "br":
            events.append((e.s, e.e, ("br",)))
        else:
            events.append((e.s, e.se, ("s", k)))
            if e.closed and e.e > e.se:
                events.append((e.ie, e.e, ("e", k)))
    events.sort()
    units, pos, skip_to = [], a, a
    for s, e, key in events:
        if s < skip_to:                    # uvnitř pevného prvku
            continue
        units += _text_units(src[pos:s])
        units.append((key, src[s:e]))
        pos = skip_to = e
    units += _text_units(src[pos:z])
    return _merge_ws(units)


class _After(HTMLParser):
    """innerHTML z prohlížeče → jednotky. Původní prvky se poznají podle data-ke-t
    a stejného tagu. Chrome při odtučnění mění <strong data-ke-t> na <span data-ke-t>
    a atribut nechá: takový prvek už původní není a rozbalí se."""

    def __init__(self, tags):
        super().__init__(convert_charrefs=True)
        self.tags = tags
        self.units, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        k = a.get("data-ke-t")
        if "data-ke-atom" in a:
            self.units.append((("a", int(k)), None))
            if tag not in VOID:
                self.skip = 1
            return
        if tag == "br":
            self.units.append((("br",), None))
            return
        if tag in VOID:
            return                         # obrázek apod. vložený prohlížečem se zahodí
        if k is not None and self.tags.get(int(k)) == tag:
            self.units.append((("s", int(k)), None))
            self.stack.append(("e", int(k)))
            return
        name = _RENAME.get(tag, tag)
        if name == "a" and a.get("href") and not safe_href(a["href"]):
            raise EditError("link", "Odkaz smí vést jen na stránku webu, kotvu, http(s), e-mail nebo telefon.")
        if name in NEW_TAGS and (name != "a" or a.get("href")):
            self.units.append((("S", name, a.get("href") or ""), None))
            self.stack.append(("E", name))
        else:
            self.stack.append(None)        # neznámý prvek (span style, rozšíření) se rozbalí

    def handle_startendtag(self, tag, attrs):
        if self.skip:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID and not self.skip:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
            return
        if tag in VOID or not self.stack:
            return
        top = self.stack.pop()
        if top is not None:
            self.units.append((top, None))

    def handle_data(self, data):
        if self.skip:
            return
        self.units += [(c, None) for c in data]

    def handle_comment(self, data):
        if not self.skip:
            self.units.append((("c", data), None))


def _last_key(units):
    return next((k for k, _ in reversed(units) if k != WS), None)


def after_units(after_html, orig_ends_br=False, tags=None):
    tags = tags or {}
    p = _After(tags)
    p.feed(after_html)
    p.close()
    units = p.units
    # Chrome si na konec editovaného prvku přidává <br> jako zarážku
    if not orig_ends_br and _last_key(units) == ("br",):
        i = max(i for i, (k, _) in enumerate(units) if k == ("br",))
        del units[i]
    return _merge_ws(units)


def reconstruct(src, block, after_html):
    """Nový zdroj inner bloku. Vyhodí EditError, když úprava porušila pevné prvky."""
    orig = orig_units(src, block)
    tags = {k: e.tag for k, e, atom in block.elems if not atom}
    after = after_units(after_html, _last_key(orig) == ("br",), tags)
    # bílé znaky na okraji bloku jsou formátování zdroje; Chrome je při psaní zahazuje
    if orig and orig[0][0] == WS and (not after or after[0][0] != WS):
        after = [(WS, None)] + after
    if orig and orig[-1][0] == WS and (not after or after[-1][0] != WS):
        after = after + [(WS, None)]
    after = _keep_ws_at_atoms(orig, after)

    def comments(units):
        return collections.Counter(k for k, _ in units if isinstance(k, tuple) and k[0] == "c")
    if comments(after) - comments(orig):
        # komentáře jsou ve zdroji značky slotů buildu (<!-- SLOT:… -->), nový by do stránky vložil obsah
        raise EditError("comment", "Komentář do textu vložit nejde.")

    atoms_o = sorted(k[1] for k, _ in orig if isinstance(k, tuple) and k[0] == "a")
    atoms_a = sorted(k[1] for k, _ in after if isinstance(k, tuple) and k[0] == "a")
    if atoms_a != atoms_o:
        missing = set(atoms_o) - set(atoms_a)
        raise EditError("atom", "Ikona, obrázek nebo číslo z dat webu se nesmí smazat ani zdvojit."
                        if not missing else "Ikona, obrázek nebo číslo z dat webu se nesmí smazat.")

    raw_of = {}
    for key, raw in orig:
        if isinstance(key, tuple) and key[0] in ("s", "e", "a") and key not in raw_of:
            raw_of[key] = raw
    br_raw = next((raw for key, raw in orig if key == ("br",)), "<br />" if "<br />" in src else "<br>")

    ka = [k for k, _ in orig]
    kb = [k for k, _ in after]
    out = []                              # [(raw, inserted, is_text)]
    for op, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if op == "equal":
            out += [(raw, False, _is_text(k)) for k, raw in orig[i1:i2]]
        elif op in ("replace", "insert"):
            for key, _ in after[j1:j2]:
                out.append((_emit(key, raw_of, br_raw), True, _is_text(key)))
    return _finish(out)


def _keep_ws_at_atoms(orig, after):
    """Bílý znak mezi textem a pevným prvkem (ikona za textem tlačítka v menu) je formátování zdroje;
    Chrome ho při psaní na konci textu zahodí. Vrátí ho tam, kde ho zdroj měl a úprava ho ztratila."""
    after = list(after)
    for i, (k, _) in enumerate(orig):
        if not (isinstance(k, tuple) and k[0] == "a"):
            continue
        j = next((x for x, (ka, _) in enumerate(after) if ka == k), None)
        if j is None:
            continue
        if i + 1 < len(orig) and orig[i + 1][0] == WS and (j + 1 >= len(after) or after[j + 1][0] != WS):
            after.insert(j + 1, (WS, None))
        if i > 0 and orig[i - 1][0] == WS and (j == 0 or after[j - 1][0] != WS):
            after.insert(j, (WS, None))
    return after


def _is_text(key):
    return isinstance(key, str) or key == WS


def _emit(key, raw_of, br_raw):
    if key == WS:
        return " "
    if isinstance(key, str):
        return _esc_text(key)
    kind = key[0]
    if kind in ("s", "e", "a"):
        return raw_of[key]
    if kind == "br":
        return br_raw
    if kind == "S":
        name, href = key[1], key[2]
        return '<a href="%s">' % html.escape(href, quote=True) if name == "a" else "<%s>" % name
    if kind == "E":
        return "</%s>" % key[1]
    if kind == "c":
        return "<!--%s-->" % key[1]
    raise ValueError(key)


def _esc_text(c):
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\u2060": "&#8288;"}.get(c, c)


def _finish(out):
    """Uvozovky ve vloženém textu a typografie v textových úsecích, kterých se
    úprava dotkla. Úseky beze změny zůstanou bajt po bajtu."""
    res, seg, touched = [], [], False

    def flush():
        nonlocal seg, touched
        text = "".join(seg)
        res.append(AD.RULES.typo(text) if touched else text)
        seg, touched = [], False

    prev = ""
    for raw, ins, is_text in out:
        if not is_text:
            flush()
            res.append(raw)
            prev = raw[-1:] if raw else prev
            continue
        if ins and raw == '"':
            raw = "„" if (not prev or prev in " (\u00a0[/;>") else "”"
        seg.append(raw)
        touched = touched or ins
        prev = raw[-1:] if raw else prev
    flush()
    return "".join(res)


def plain(s):
    """Prostý text pro JSON-LD a meta: bez tagů, bez NBSP a word joinerů,
    rovné uvozovky na „…” (P02), U+201C na U+201D (verify ho v JSON-LD nepustí)."""
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("\u00a0", " ").replace("\u2060", "").replace("“", "”")
    s = re.sub(r"\s+", " ", s).strip()
    out, prev = [], ""
    for c in s:
        if c == '"':
            c = "„" if (not prev or prev in " ([/") else "”"
        out.append(c)
        prev = c
    return "".join(out)


def norm(s):
    """Porovnávací tvar textu: bez tagů, NBSP a WJ jako mezera/nic, bílé znaky sloučené."""
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("\u00a0", " ").replace("\u2060", "")
    return re.sub(r"\s+", " ", s).strip()
