# -*- coding: utf-8 -*-
"""Pokrytí textů webu: co jde v editoru upravit klikem nebo v panelu, co je sdílené, co počítá
build a co je zamčené a proč. Počítá nad classify(page), ze které editor bere zámky bloků, takže
report a editor nemůžou mluvit jinak.

  python cms/coverage.py                   report (owner stránky jako po odemčení adminem)
  python cms/coverage.py --json out.json   navíc strojově
  python cms/coverage.py --pages           navíc po stránkách

Viditelný text = text a SVG v <body>. Unikátní znaky = součet délek různých textů v podkategorii
(text z víc stránek se počítá jednou), stejně jako sonda A0 v ADR 0014 (64 % ruční HTML, 26 %
Python, 10 % JSON, 0,8 % hlavička a patička). Generované stránky potřebují zkušební build
(proveniance bloků a polí SEO), report proto běží desítky sekund.

Podkategorie a citace specifické pro web (popisky v reportu) dodává adaptér webu (adapter.RULES).
"""
import argparse
import collections
import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import adapter as AD

VISIBLE = ("text", "svg")
GROUPS = [("html", "ruční HTML"), ("python", "Python"), ("json", "JSON"), ("chrome", "hlavička a patička")]
DETAIL = {"handbuilt": "ruční", "owner": "owner", "faq-from": "převzaté FAQ", "script": "skripty",
          "quote": "citace", "header": "hlavička", "footer": "patička"}
STATES = [("klik", "klik"), ("sdileny", "sdílený"), ("panel", "panel"), ("pocitany", "počítaný"),
          ("zamceny", "zamčený")]
KINDS = [("attr", "Atributy"), ("head", "Hlavička stránky"), ("jsonld", "JSON-LD"),
         ("data", "Data pro skripty"), ("js", "JavaScript")]


def collect(root, jobs=4):
    """({slug: [Item]} všech stránek z registru, {soubor: [Item]} skriptů webu)."""
    import apply as A
    import classify as C
    tmp = tempfile.mkdtemp(prefix="cms-coverage-")
    try:
        ed = A.Editor(root, journal_dir=tmp)

        def one(slug):
            return slug, C.classify(C.page(ed, slug, unlock=True))

        with ThreadPoolExecutor(jobs) as ex:
            pages = dict(ex.map(one, sorted(ed.registry())))
        scripts = {}
        for f in sorted(ed.dist.glob("*.js")):
            rel = f.relative_to(ed.root).as_posix()
            scripts[rel] = [C.Item("js", f.name, "js", f.name, C.ZAMCENY, C.WHY_JS, t, at)
                            for at, t in C.js_texts(f.read_text(encoding="utf-8"))]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return pages, scripts


def _chars(texts):
    return sum(len(t) for t in texts)


def _quotes(attrs):
    """Popisek řádku citací: citace (data-quote, -name, -meta)."""
    return "citace (%s)" % ", ".join([attrs[0]] + ["-" + a[5:] if a.startswith("data-") else a for a in attrs[1:]])


def _where(it):
    """Řádek tabulky ostatních textů."""
    if it.kind == "attr":
        quotes = AD.RULES.quote_attrs
        return _quotes(quotes) if it.where in quotes else (
            "data-* pro skripty" if it.where.startswith("data-") else it.where)
    if it.kind == "head":
        return it.where
    if it.kind == "jsonld":
        return "FAQ" if it.where in ("Question.name", "Answer.text") else "ostatní"
    if it.kind == "data":
        return it.where or "?"
    return "inline" if it.where == "inline" else "soubory .js webu"


def summary(pages, scripts):
    """Souhrn pro výpis i JSON."""
    src_u, src_o = collections.defaultdict(set), collections.Counter()
    st_u, st_o = collections.defaultdict(set), collections.Counter()
    reasons = collections.defaultdict(set)
    other = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    other_u = collections.defaultdict(lambda: collections.defaultdict(set))
    per_page = {}
    items = [(slug, it) for slug, its in pages.items() for it in its]
    items += [(None, it) for its in scripts.values() for it in its]
    for slug, it in items:
        if it.kind in VISIBLE:
            src_u[(it.source, it.detail)].add(it.text)
            src_o[(it.source, it.detail)] += len(it.text)
            st_u[it.state].add(it.text)
            st_o[it.state] += len(it.text)
            if it.state == "zamceny":
                reasons[it.why].add(it.text)
            if slug:
                pp = per_page.setdefault(slug, collections.Counter())
                pp[it.state] += len(it.text)
        else:
            w = _where(it)
            other[it.kind][w][it.state] += 1
            other_u[it.kind][w].add(it.text)
    total = sum(_chars(v) for v in src_u.values())

    def pct(x, of=None):
        return round(100.0 * x / (of if of is not None else total), 1) if (of if of is not None else total) else 0.0

    groups = {}
    for g, _ in GROUPS:
        det = {d: _chars(v) for (s, d), v in src_u.items() if s == g}
        u = sum(det.values())
        groups[g] = {"share": pct(u), "chars": u, "rendered": sum(o for (s, _), o in src_o.items() if s == g),
                     "detail": {d: pct(c) for d, c in sorted(det.items(), key=lambda x: -x[1])}}
    st_total = sum(_chars(v) for v in st_u.values())
    states = {s: {"share": pct(_chars(st_u[s]), st_total), "chars": _chars(st_u[s]), "rendered": st_o[s]}
              for s, _ in STATES}
    rs = sorted(((why, _chars(t)) for why, t in reasons.items()), key=lambda x: -x[1])
    return {
        "pages": len(pages),
        "scripts": sorted(scripts),
        "visible": {"chars": total, "source": groups, "state": states, "state_chars": st_total,
                    "locked": [{"why": w, "share": pct(c, st_total), "chars": c} for w, c in rs]},
        "other": {k: {w: dict(cnt, unique=len(other_u[k][w])) for w, cnt in sorted(other[k].items())}
                  for k, _ in KINDS if k in other},
        "pages_state": {slug: {s: round(100.0 * c[s] / sum(c.values()), 1) for s, _ in STATES if c[s]}
                        for slug, c in sorted(per_page.items())},
    }


def _cz(x):
    return ("%.1f" % x).replace(".", ",")


def render(sm, pages=False):
    v = sm["visible"]
    out = ["Pokrytí textů: %d stránek (owner jako po odemčení), viditelný text %s unikátních znaků"
           % (sm["pages"], "{:,}".format(v["chars"]).replace(",", " ")), "",
           "Kde žije viditelný text (podíl unikátních znaků)"]
    for g, label in GROUPS:
        x = v["source"][g]
        labels = dict(DETAIL, **AD.RULES.labels)
        det = " · ".join("%s %s" % (labels.get(d, d), _cz(p)) for d, p in x["detail"].items() if p >= 0.1)
        out.append("  %-20s %5s %%   %s" % (label, _cz(x["share"]), det))
    out += ["", "Jak jde upravit viditelný text (podíl unikátních znaků)"]
    for s, label in STATES:
        if v["state"][s]["chars"]:
            out.append("  %-20s %5s %%" % (label, _cz(v["state"][s]["share"])))
    out += ["", "Zamčený viditelný text podle důvodu"]
    for r in v["locked"]:
        if r["share"] >= 0.1:
            out.append("  %5s %%  %s" % (_cz(r["share"]), r["why"]))
    rest = [r for r in v["locked"] if r["share"] < 0.1]
    if rest:
        out.append("  %5s %%  dalších %d důvodů pod 0,1 %%" % (_cz(sum(r["share"] for r in rest)), len(rest)))
    out += ["", "Ostatní texty: počet výskytů (různých textů)"]
    out.append("  %-34s %s" % ("", "".join("%10s" % label for _, label in STATES)))
    for k, label in KINDS:
        if k not in sm["other"]:
            continue
        out.append("  " + label)
        for w, cnt in sm["other"][k].items():
            cells = "".join("%10s" % (cnt.get(s) or "") for s, _ in STATES)
            out.append("    %-32s %s   (%d)" % (w[:32], cells, cnt["unique"]))
    if pages:
        out += ["", "Po stránkách: podíl viditelného textu (výskyty)"]
        for slug, st in sm["pages_state"].items():
            out.append("  %-46s %s" % (slug, " · ".join("%s %s" % (dict(STATES)[s], _cz(p)) for s, p in st.items())))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pokrytí textů webu v editoru.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="kořen repa webu")
    ap.add_argument("--json", help="soubor pro strojový výstup")
    ap.add_argument("--pages", action="store_true", help="i po stránkách")
    ap.add_argument("--jobs", type=int, default=4, help="souběžné zkušební buildy")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    sm = summary(*collect(root, a.jobs))
    print(render(sm, a.pages))
    if a.json:
        Path(a.json).write_text(json.dumps(sm, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
