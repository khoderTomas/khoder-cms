# -*- coding: utf-8 -*-
"""Šev mezi motorem a webem: cms/ je obecný balíček, web se mu ukazuje jen přes adaptér
(cms.toml [editor] adapter, adapter.py).

- moduly cms/ importují jen standardní knihovnu a sebe navzájem (ne build/ ani hostitele);
- kód motoru (py, js, css, spouštěč; bez testů) nemá cesty webu (build/, content/) a nesahá mimo repo;
- v celém cms/ není „khoder“ kromě názvu balíčku Khoder CMS (LICENSE nese jméno autora, .git není balíček).

  python -m unittest discover -s cms/tests -t cms/tests -p "test_seam.py"
"""
import ast
import re
import sys
import unittest
from pathlib import Path

CMS = Path(__file__).resolve().parents[1]
# tenhle test nese vzory, které hledá, sám se neprohledává
FILES = [p for p in CMS.rglob("*") if p.is_file() and not {"__pycache__", ".git"} & set(p.relative_to(CMS).parts)
         and p.suffix != ".pyc" and p != Path(__file__).resolve() and p.relative_to(CMS).as_posix() != "LICENSE"]
CODE = [p for p in FILES if p.suffix in (".py", ".js", ".css") or p.parent.name == "bin"]
ENGINE = [p for p in CODE if p.parent.name != "tests"]       # testy mají syntetická repa s vlastními cestami
OWN = {p.stem for p in CMS.glob("*.py")} | {p.stem for p in (CMS / "tests").glob("*.py")}
SITE_PATH = re.compile(r"\b(?:build|content)/|parents\[[2-9]\]")
KHODER = re.compile(r"khoder(?![ -]cms)", re.I)


def rel(p):
    return p.relative_to(CMS).as_posix()


class SeamTest(unittest.TestCase):
    def test_imports_only_stdlib_and_cms(self):
        bad = []
        for f in (p for p in CODE if p.suffix == ".py"):
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module]
                else:
                    continue
                bad += ["%s:%d %s" % (rel(f), n.lineno, x) for x in names
                        if x.split(".")[0] not in sys.stdlib_module_names and x.split(".")[0] not in OWN]
        self.assertEqual(bad, [])

    def test_no_site_paths(self):
        bad = ["%s:%d" % (rel(f), i) for f in ENGINE
               for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if SITE_PATH.search(line)]
        self.assertEqual(bad, [])

    def test_no_khoder(self):
        bad = ["%s:%d" % (rel(f), i) for f in FILES
               for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
               if KHODER.search(line)]
        self.assertEqual(bad, [])

    def test_patterns_catch_site(self):
        """Pojistka testu: vzory opravdu chytí, co mají (negativní test bez pozitivu nic nedokazuje)."""
        self.assertTrue(SITE_PATH.search('sys.path.insert(0, "build/admin")'))
        self.assertTrue(SITE_PATH.search("Path(__file__).resolve().parents[2]"))
        self.assertTrue(KHODER.search("https://www.khoder.cz"))
        self.assertFalse(KHODER.search("Khoder CMS"))


if __name__ == "__main__":
    unittest.main()
