# -*- coding: utf-8 -*-
"""Hodnoty v textu JSON s polohou: zápis hodnoty na cestě bez přeformátování zbytku.

JSON-LD ručních stránek má každý blok zapsaný jinak (seznam na jednom řádku, nebo položka na
řádek, různé odsazení a pořadí klíčů). Editor i build proto mění jen hodnotu na cestě: řetězec
se zapíše jako json.dumps(ensure_ascii=False) s „</“ jako „<\\/“ (text nesmí ukončit <script>),
seznam řetězců ve stylu původního seznamu (na jednom řádku se stejným oddělovačem, nebo položka
na řádek se stejným odsazením), jiná hodnota jako json.dumps s odsazením 2 od řádku, na kterém
stojí. Zpětné parsování musí dát strom, kde se liší jen ta cesta, jinak JsonSourceError.

Cesta je n-tice klíčů objektů a indexů seznamů, () je celý dokument.

Datové soubory webu (texty v JSON, B8): strings dá řetězcové hodnoty s polohou, write_string zapíše
jednu hodnotu v původním escapování tokenu (nezměněné znaky zůstanou původními bajty, nové neviditelné
znaky jako \\uXXXX, P31) a merge3 sloučí tři verze souboru po cestách, když git merge-file po řádcích
hlásí konflikt (dvě hodnoty na jednom řádku).
"""
import json
import re
import unicodedata
from difflib import SequenceMatcher

_WS = re.compile(r"[ \t\r\n]*")
_STR = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_LIT = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


class JsonSourceError(ValueError):
    pass


def enc(value):
    """Hodnota jako JSON pro <script>: json.dumps bez escapování češtiny, „</“ s escapovaným lomítkem."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def spans(raw):
    """{cesta: (začátek, konec)} všech hodnot v textu raw."""
    out = {}

    def ws(i):
        return _WS.match(raw, i).end()

    def bad(i, what):
        raise JsonSourceError("JSON na pozici %d: čekám %s" % (i, what))

    def value(i, path):
        i = ws(i)
        c = raw[i:i + 1]
        a = i
        if c in "{[" and c:
            close = "}" if c == "{" else "]"
            i = ws(i + 1)
            n = 0
            if raw[i:i + 1] != close:
                while True:
                    if c == "{":
                        m = _STR.match(raw, i)
                        if not m:
                            bad(i, "klíč")
                        key = json.loads(m.group(0))
                        i = ws(m.end())
                        if raw[i:i + 1] != ":":
                            bad(i, "dvojtečku")
                        i = value(i + 1, path + (key,))
                    else:
                        i = value(i, path + (n,))
                    n += 1
                    i = ws(i)
                    if raw[i:i + 1] == ",":
                        i = ws(i + 1)
                        continue
                    if raw[i:i + 1] == close:
                        break
                    bad(i, "čárku nebo %s" % close)
            out[path] = (a, i + 1)
            return i + 1
        m = (_STR if c == '"' else _LIT).match(raw, i)
        if not m:
            bad(i, "hodnotu")
        out[path] = m.span()
        return m.end()

    end = value(0, ())
    if ws(end) != len(raw):
        bad(ws(end), "konec")
    return out


def get(tree, path):
    for p in path:
        tree = tree[p]
    return tree


def _line_indent(raw, a):
    """Odsazení řádku, na kterém stojí pozice a."""
    start = raw.rfind("\n", 0, a) + 1
    return re.match(r"[ \t]*", raw[start:a]).group(0)


def _list_like(old, items_old, new, base):
    """Seznam řetězců new ve stylu seznamu old (text od [ do ]; items_old = poloha položek v něm)."""
    if not new:
        return "[]"
    parts = [enc(x) for x in new]
    if "\n" in old:
        if items_old:
            a = items_old[0][0]
            ind = old[old.rfind("\n", 0, a) + 1:a]
        else:
            ind = base + "  "
        close = old[old.rfind("\n") + 1:-1]
        if close.strip():
            close = base
        return "[\n" + ",\n".join(ind + p for p in parts) + "\n" + close + "]"
    if len(items_old) >= 2:
        sep = old[items_old[0][1]:items_old[1][0]]
    else:
        sep = ", "
    lead = old[1:items_old[0][0]] if items_old else ""
    trail = old[items_old[-1][1]:-1] if items_old else ""
    return "[" + lead + sep.join(parts) + trail + "]"


def _encode_like(raw, a, z, new):
    old = raw[a:z]
    if isinstance(new, str):
        return enc(new)
    if isinstance(new, list) and all(isinstance(x, str) for x in new) and old.startswith("["):
        sub = spans(old)
        items = [sub[(i,)] for i in range(len([k for k in sub if len(k) == 1]))]
        return _list_like(old, items, new, _line_indent(raw, a))
    base = _line_indent(raw, a)
    text = json.dumps(new, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return text.replace("\n", "\n" + base)


def replace(raw, path, new):
    """raw s hodnotou new na cestě path, ostatní text beze změny."""
    sp = spans(raw)
    if path not in sp:
        raise JsonSourceError("V JSON chybí cesta %s." % "/".join(map(str, path)))
    a, z = sp[path]
    out = raw[:a] + _encode_like(raw, a, z, new) + raw[z:]
    want = json.loads(raw)
    if path:
        get(want, path[:-1])[path[-1]] = new
    else:
        want = new
    if json.loads(out) != want:
        raise JsonSourceError("Zápis na cestu %s změnil i jiné hodnoty." % "/".join(map(str, path)))
    return out


# ── řetězce datových souborů: poloha, zápis v původním escapování, sloučení po cestách ──
_ESC = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_ENC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def strings(raw):
    """[(cesta, začátek, konec, hodnota)] řetězcových hodnot (klíče ne) v pořadí textu; rozsah je
    token i s uvozovkami."""
    out = [(p, a, z) for p, (a, z) in spans(raw).items() if raw[a:a + 1] == '"']
    out.sort(key=lambda x: x[1])
    return [(p, a, z, json.loads(raw[a:z])) for p, a, z in out]


def _pieces(tok):
    """[(znak, bajty v tokenu)] obsahu tokenu "…" (dvojice náhradních znaků \\uD83D\\uDE00 jako jeden znak)."""
    out, i, end = [], 1, len(tok) - 1
    while i < end:
        c = tok[i]
        if c != "\\":
            out.append((c, c))
            i += 1
            continue
        e = tok[i + 1]
        if e != "u":
            out.append((_ESC[e], tok[i:i + 2]))
            i += 2
            continue
        cp = int(tok[i + 2:i + 6], 16)
        j = i + 6
        if 0xD800 <= cp < 0xDC00 and tok[j:j + 2] == "\\u" and 0xDC00 <= int(tok[j + 2:j + 6], 16) < 0xE000:
            cp = 0x10000 + ((cp - 0xD800) << 10) + (int(tok[j + 2:j + 6], 16) - 0xDC00)
            j += 6
        out.append((chr(cp), tok[i:j]))
        i = j
    return out


def _invisible(c):
    return c != " " and unicodedata.category(c) in ("Zs", "Zl", "Zp", "Cf", "Cc", "Co")


def _enc_char(c, esc, upper):
    if c in _ENC:
        return _ENC[c]
    if c in esc or _invisible(c):
        if ord(c) > 0xFFFF:
            v = ord(c) - 0x10000
            return _enc_char(chr(0xD800 + (v >> 10)), (), upper) + _enc_char(chr(0xDC00 + (v & 0x3FF)), (), upper)
        h = "%04x" % ord(c)
        return "\\u" + (h.upper() if upper else h)
    return c


def write_string(raw, path, new):
    """raw s řetězcem new na cestě path: zapíše se jen změna proti původní hodnotě, nezměněné znaky
    původními bajty tokenu, nové znaky ve stylu tokenu (znak, který token escapoval, a neviditelné
    znaky jako \\uXXXX, jinak doslova). Zpětné parsování musí změnit jen tu cestu."""
    sp = spans(raw)
    if path not in sp:
        raise JsonSourceError("V JSON chybí cesta %s." % "/".join(map(str, path)))
    a, z = sp[path]
    tok = raw[a:z]
    if not tok.startswith('"'):
        raise JsonSourceError("Na cestě %s není text." % "/".join(map(str, path)))
    pieces = _pieces(tok)
    old = "".join(c for c, _ in pieces)
    esc = {c for c, r in pieces if r.startswith("\\u")}
    upper = any(r.startswith("\\u") and r[2:6] != r[2:6].lower() for _, r in pieces)
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            out += [r for _, r in pieces[i1:i2]]
        elif op in ("replace", "insert"):
            out += [_enc_char(c, esc, upper) for c in new[j1:j2]]
    res = raw[:a] + '"' + "".join(out) + '"' + raw[z:]
    want = json.loads(raw)
    if path:
        get(want, path[:-1])[path[-1]] = new
    else:
        want = new
    if json.loads(res) != want:
        raise JsonSourceError("Zápis na cestu %s změnil i jiné hodnoty." % "/".join(map(str, path)))
    return res


def _changes(a, b, path=()):
    """Cesty, na kterých se strom b liší od a (objekt se stejnými klíči a seznam se stejnou délkou
    po položkách, jinak celá hodnota)."""
    if isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b):
        return [p for k in a for p in _changes(a[k], b[k], path + (k,))]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _changes(x, y, path + (i,))]
    return [] if a == b and type(a) is type(b) else [path]


def merge3(ours, base, theirs):
    """Třícestné sloučení textů JSON po cestách: změny ours proti base se zapíšou do theirs jako
    původní text hodnot z ours (formát theirs zůstane). None, když se změněné cesty ours a theirs
    překrývají s jinou hodnotou, nebo když některá verze není platný JSON."""
    try:
        o, b, t = json.loads(ours), json.loads(base), json.loads(theirs)
    except ValueError:
        return None
    co, ct = _changes(b, o), _changes(b, t)
    for p in co:
        for q in ct:
            if p[:len(q)] == q or q[:len(p)] == p:
                if p != q or get(o, p) != get(t, q):
                    return None
    todo = [p for p in co if p not in ct]
    if not todo:
        return theirs
    so, st = spans(ours), spans(theirs)
    if any(p not in st for p in todo):
        return None
    out, want = theirs, t
    for p in sorted(todo, key=lambda p: -st[p][0]):
        a, z = st[p]
        oa, oz = so[p]
        out = out[:a] + ours[oa:oz] + out[z:]
        if p:
            get(want, p[:-1])[p[-1]] = get(o, p)
        else:
            want = get(o, p)
    if json.loads(out) != want:
        return None
    return out


def sync(raw, path, want):
    """raw, kde má cesta path hodnotu want: v objektech se stejnými klíči se přepíšou jen hodnoty,
    které se liší (pořadí klíčů a formát zůstanou), jinak celá hodnota (replace)."""
    cur = get(json.loads(raw), path)
    if cur == want and type(cur) is type(want):
        return raw
    if isinstance(want, dict) and isinstance(cur, dict) and set(want) == set(cur):
        for k in want:
            raw = sync(raw, path + (k,), want[k])
        return raw
    return replace(raw, path, want)
