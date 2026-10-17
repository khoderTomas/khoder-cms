# -*- coding: utf-8 -*-
"""Build a kontrola ukázkového webu: stránky z content/pages.json projdou kroky buildu (build/web.py)
a zapíšou se na místě, jen když se změnily; pak kontrola celého webu. Chyba = exit 1, nic se nenasadí.

  python build/build.py            build + kontrola
  python build/build.py --verify   jen kontrola

Build drží zámek buildu Khoder CMS (cms/buildlock.py), když je motor vedle webu: build a uložení
z editoru se tak nepřekryjí.
"""
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web as W  # noqa: E402


def engine():
    """Složka motoru Khoder CMS: cms/ v repu webu, nebo repo motoru, ve kterém je web ukázkou."""
    for p in (W.ROOT / "cms", W.ROOT.parent):
        if (p / "buildlock.py").is_file():
            return p
    return None


def lock():
    cms = engine()
    if cms is None:
        return contextlib.nullcontext()
    sys.path.insert(0, str(cms))
    import buildlock
    return buildlock.hold("build/build.py")


def build(data):
    changed = 0
    manifest = data.manifest()
    for slug in sorted(manifest):
        path = data.root / data.source / (slug + ".html")
        if not path.is_file():
            continue                              # chybějící stránku ohlásí kontrola
        with open(path, encoding="utf-8", newline="") as f:
            cur = f.read()
        new = W.render(slug, cur, data)
        if new != cur:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed += 1
    print("build: %d stránek, %d souborů aktualizováno" % (len(manifest), changed))


def main(argv):
    with lock():
        try:
            if "--verify" not in argv:
                build(W.Data())
            data = W.Data()
            fails = W.verify(data)
        except W.BuildError as e:
            print("BUILD FAILED: %s" % e)
            return 1
    pages = len(list((data.root / data.source).glob("*.html")))
    print("verify: %d stránek | %d chyb" % (pages, len(fails)))
    for x in fails:
        print("  - " + x)
    if fails:
        print("BUILD FAILED, nenasazovat.")
        return 1
    print("BUILD OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
