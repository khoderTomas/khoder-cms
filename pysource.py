# -*- coding: utf-8 -*-
"""Textové literály v Python zdrojích generátoru stránek (skripty a sdílené soubory).

Literál = obsah řetězcového tokenu mezi uvozovkami, nebo textová část f-stringu
mezi replacement fieldy. U f-stringů se čte surový zdroj mezi FSTRING_START
a FSTRING_END, protože offsety FSTRING_MIDDLE v Pythonu 3.12+ kolem {{ nesedí.

Zápis mění jen raw rozsah jednoho literálu: nová hodnota se zakóduje ve stylu
původních uvozovek a ověří zpětným vyhodnocením.
"""
import ast
import io
import re
import tokenize

_PREFIX = re.compile(r"^([rRbBuUfF]*)('''|\"\"\"|'|\")")
# slug, klíč, třída, cesta: do takového literálu se značka ani úprava nevkládá
IDENT = re.compile(r"^[a-z0-9_\-/.#:]*$")


class Lit:
    __slots__ = ("a", "z", "value", "q", "raw", "fstr", "line")

    def __init__(self, a, z, value, q, raw, fstr, line):
        self.a, self.z = a, z              # raw rozsah obsahu v souboru (bez uvozovek)
        self.value, self.q = value, q      # dekódovaná hodnota, uvozovky (' " ''' """)
        self.raw, self.fstr, self.line = raw, fstr, line

    def __repr__(self):
        return "Lit(line %d, %r)" % (self.line, self.value[:40])


def _offsets(src):
    starts = [0]
    for m in re.finditer("\n", src):
        starts.append(m.end())
    return lambda rc: starts[rc[0] - 1] + rc[1]


def literals(src):
    """Všechny textové literály souboru v pořadí zdroje."""
    off = _offsets(src)
    out = []
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    fs_start = getattr(tokenize, "FSTRING_START", None)
    fs_end = getattr(tokenize, "FSTRING_END", None)
    depth, fstart = 0, None
    for t in toks:
        if t.type == tokenize.STRING and depth == 0:
            m = _PREFIX.match(t.string)
            prefix, q = m.group(1).lower(), m.group(2)
            if "b" in prefix:
                continue
            a = off(t.start) + len(m.group(0))
            z = off(t.end) - len(q)
            try:
                v = ast.literal_eval(t.string)
            except (ValueError, SyntaxError):
                continue
            if "f" in prefix:              # Python < 3.12: f-string jako jeden STRING
                out += _fparts(src, a, z, q, "r" in prefix, t.start[0])
            else:
                out.append(Lit(a, z, v, q, "r" in prefix, False, t.start[0]))
        elif t.type == fs_start:
            if depth == 0:
                fstart = t
            depth += 1
        elif t.type == fs_end:
            depth -= 1
            if depth == 0:
                m = _PREFIX.match(fstart.string)
                q = m.group(2)
                a = off(fstart.start) + len(fstart.string)
                z = off(t.start)
                out += _fparts(src, a, z, q, "r" in m.group(1).lower(), fstart.start[0])
    return out


def _fparts(src, a, z, q, raw, line):
    """Textové části f-stringu src[a:z] jako samostatné literály."""
    parts, i, start = [], a, a
    while i < z:
        c = src[i]
        if c == "\\" and not raw:
            i += 2
            continue
        if src.startswith("{{", i) or src.startswith("}}", i):
            i += 2
            continue
        if c == "{":
            if i > start:
                parts.append((start, i))
            i = _field_end(src, i, z)
            start = i
            continue
        i += 1
    if z > start:
        parts.append((start, z))
    out = []
    for pa, pz in parts:
        body = src[pa:pz].replace("{{", "{").replace("}}", "}")
        try:
            v = ast.literal_eval(("r" if raw else "") + q + body + q)
        except (ValueError, SyntaxError):
            continue
        out.append(Lit(pa, pz, v, q, raw, True, line + src.count("\n", a, pa)))
    return out


def _field_end(src, i, z):
    depth, j = 0, i
    while j < z:
        c = src[j]
        if c in "'\"":
            k = src.find(c, j + 1)
            j = (k if k != -1 else z) + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return z


def encode(lit, value):
    """Raw obsah literálu pro novou hodnotu ve stylu původních uvozovek."""
    q = lit.q
    triple = len(q) == 3
    raw = lit.raw
    if raw and (q[0] in value or value.endswith("\\") or ("\n" in value and not triple)):
        raise ValueError("raw literál neumí tuhle hodnotu")
    if raw:
        s = value
    else:
        s = value.replace("\\", "\\\\")
        if triple:
            s = s.replace(q[0] * 3, "\\" + q[0] * 3)
            if s.endswith(q[0]):
                s = s[:-1] + "\\" + q[0]
        else:
            s = s.replace(q, "\\" + q).replace("\n", "\\n").replace("\r", "\\r")
    if lit.fstr:
        s = s.replace("{", "{{").replace("}", "}}")
    check = s.replace("{{", "{").replace("}}", "}") if lit.fstr else s
    if ast.literal_eval(("r" if raw else "") + q + check + q) != value:
        raise ValueError("literál se nepodařilo zakódovat")
    return s


def replace(src, lit, value):
    """Zdroj s novou hodnotou literálu (mění se jen jeho raw rozsah).
    Víceřádkový literál v souboru s CRLF (checkout s autocrlf) dostane
    konce řádků CRLF: hodnota je má po vyhodnocení jako LF a soubor by byl smíšený."""
    s = encode(lit, value)
    if len(lit.q) == 3 and "\r\n" in src:
        s = s.replace("\r\n", "\n").replace("\n", "\r\n")
    return src[:lit.a] + s + src[lit.z:]
