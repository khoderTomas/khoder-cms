# -*- coding: utf-8 -*-
"""Souhrn reportu pokrytí (cms/coverage.py) nad ručně složenými položkami.

  python -m unittest discover -s cms/tests -t cms/tests -p "test_coverage.py"
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter as AD   # noqa: E402
import coverage as CV  # noqa: E402


class Rules(AD.Rules):
    quote_attrs = ("data-quote", "data-name", "data-meta")
    labels = {"wave": "vlny"}


def it(kind, text, source="html", detail="handbuilt", state="klik", why=None, where=None):
    return SimpleNamespace(kind=kind, text=text, source=source, detail=detail, state=state, why=why, where=where)


class SummaryTest(unittest.TestCase):
    def setUp(self):
        prev = AD.RULES
        AD.install(Rules())
        self.addCleanup(AD.install, prev)
        menu = it("text", "Menu", "chrome", "header", "zamceny", "Menu je sdílené.")
        self.pages = {
            "a": [it("text", "aaaaaa"), menu, it("text", "jjjj", "json", "pricing", "zamceny", "Ceník."),
                  it("attr", "Obrázek", state="zamceny", why="Atribut.", where="alt")],
            "b": [it("text", "aaaaaa"), menu, it("text", "pppppppp", "python", "wave", "sdileny", "Sdílený."),
                  it("attr", "Citace", state="zamceny", why="Citace.", where="data-quote")],
        }
        self.scripts = {"dist/app.js": [it("js", "Odesílám", "js", "app.js", "zamceny", "Skript.", "app.js")]}

    def test_unique_chars_and_shares(self):
        sm = CV.summary(self.pages, self.scripts)
        v = sm["visible"]
        self.assertEqual(v["chars"], 6 + 4 + 4 + 8)               # text na dvou stránkách jednou
        self.assertEqual(v["source"]["html"]["chars"], 6)
        self.assertEqual(v["source"]["html"]["rendered"], 12)
        self.assertEqual(v["source"]["chrome"]["share"], round(100 * 4 / 22, 1))
        self.assertEqual(v["state"]["zamceny"]["chars"], 8)
        self.assertEqual([r["why"] for r in v["locked"]], ["Menu je sdílené.", "Ceník."])
        self.assertEqual(sm["other"]["attr"]["alt"]["zamceny"], 1)
        self.assertIn("citace (data-quote, -name, -meta)", sm["other"]["attr"])
        self.assertEqual(sm["other"]["js"]["soubory .js webu"]["unique"], 1)
        self.assertEqual(sm["pages_state"]["a"], {"klik": 42.9, "zamceny": 57.1})

    def test_render_has_all_groups(self):
        out = CV.render(CV.summary(self.pages, self.scripts), pages=True)
        for label in ("ruční HTML", "Python", "JSON", "hlavička a patička", "zamčený", "Atributy", "JavaScript",
                      "Po stránkách", "vlny"):
            self.assertIn(label, out)


if __name__ == "__main__":
    unittest.main()
