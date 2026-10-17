# -*- coding: utf-8 -*-
"""Hodnoty v textu JSON s polohou: zápis hodnoty na cestě bez přeformátování zbytku.

JSON-LD ručních stránek má každý blok zapsaný jinak (seznam na jednom řádku, nebo položka na
řádek, různé odsazení a pořadí klíčů). Editor i build proto mění jen hodnotu na cestě: řetězec
se zapíše jako json.dumps(ensure_ascii=False) s „</“ jako „<\\/“ (text nesmí ukončit <script>),
seznam řetězců ve stylu původního seznamu (na jednom řádku se stejným oddělovačem, nebo položka
na řádek se stejným odsazením), jiná hodnota jako json.dumps s odsazením 2 od řádku, na kterém
stojí. Zpětné parsování musí dát strom, kde se liší jen ta cesta, jinak JsonSourceError.

Cesta je n-tice klíčů objektů a indexů seznamů, () je celý dokument.
"""
import json
import re

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
