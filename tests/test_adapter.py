# -*- coding: utf-8 -*-
"""Adaptér webu: načtení podle cms.toml, výchozí web bez buildu a pravidla pro proces.

  python -m unittest discover -s cms/tests -t cms/tests -p "test_adapter.py"
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter as AD   # noqa: E402
import blocks as B     # noqa: E402

ADAPTER = '''
import adapter as CA


class Rules(CA.Rules):
    lock_classes = {"cena": "Cena se mění v datech."}

    def regions(self, src):
        a = src.find("<!-- DATA -->")
        return [(a, src.index("<!-- /DATA -->") + 14, "Oblast z dat.")] if a >= 0 else []


class Site(CA.Site):
    rules = Rules()
    contract = 1

    def shared_files(self):
        return ["gen/sablona.py"]

    def scripts(self):
        return ["gen/a.py", "gen/b.py"]
'''


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cms-adapter-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        prev = AD.RULES
        self.addCleanup(AD.install, prev)
        (self.tmp / "www").mkdir()
        (self.tmp / "pages.json").write_text(json.dumps({"index": {"kind": "handbuilt", "output": "www/index.html"}}),
                                             encoding="utf-8")

    def config(self, extra=""):
        (self.tmp / "cms.toml").write_text('[deploy]\nsource = "www"\nsite = "https://example.test/"\n'
                                           'pages = "pages.json"\n' + extra, encoding="utf-8")

    def test_default_site_without_adapter(self):
        self.config()
        site = AD.load(self.tmp)
        self.assertIs(type(site), AD.Site)
        self.assertIs(type(AD.RULES), AD.Rules)
        self.assertEqual((site.url, site.page_rel("index"), site.page_slug("www/index.html")),
                         ("https://example.test", "www/index.html", "index"))
        self.assertIsNone(site.page_slug("gen/a.py"))
        self.assertEqual(list(site.registry()), ["index"])
        self.assertEqual(site.inject("<p>x</p>", "index"), "<p>x</p>")
        self.assertEqual(site.generate(), (True, "", {}))
        with self.assertRaises(AD.SiteError):
            site.generate({"gen/a.py": "x"})
        self.assertEqual(site.build_hint(), "build webu")
        # web bez sdílených částí stránek: Najít a nahradit nemá další zdroje a stránky nic nepřeskočí
        self.assertEqual((site.partial_files(), site.partial_pages("x.html"), AD.RULES.partial_spans("<p>x</p>")),
                         ([], [], []))
        with site.overlay({"x.html": "<p>y</p>"}):
            self.assertEqual(site.inject("<p>x</p>", "index"), "<p>x</p>")

    def test_adapter_from_config_installs_rules(self):
        (self.tmp / "sitex_adapter.py").write_text(ADAPTER, encoding="utf-8")
        self.config('[editor]\nadapter = "sitex_adapter.py"\n[publish]\nbuild = ["make.py", "--all"]\n')
        self.addCleanup(sys.modules.pop, "sitex_adapter", None)
        site = AD.load(self.tmp)
        self.assertEqual(type(site).__module__, "sitex_adapter")
        self.assertEqual(site.build_hint(), "python make.py --all")
        src = '<body><p class="cena">100 Kč</p><!-- DATA --><p>Z dat</p><!-- /DATA --><p>Volný text</p></body>'
        locks = [(B.block_text(src, b).strip(), b.lock) for b in B.blocks(src)]
        self.assertEqual(locks, [("100 Kč", "Cena se mění v datech."), ("Z dat", "Oblast z dat."), ("Volný text", None)])

    def test_adapter_for_other_contract_is_refused(self):
        for name, line in (("sitey_adapter", "    contract = 2\n"), ("sitez_adapter", "")):
            (self.tmp / (name + ".py")).write_text(ADAPTER.replace("    contract = 1\n", line), encoding="utf-8")
            self.config('[editor]\nadapter = "%s.py"\n' % name)
            self.addCleanup(sys.modules.pop, name, None)
            with self.assertRaises(AD.ContractError) as cm:
                AD.load(self.tmp)
            self.assertIn("%s.py" % name, str(cm.exception))
            self.assertIn("rozhraní %d" % AD.CONTRACT, str(cm.exception))
            self.assertIn("UPGRADING.md", str(cm.exception))
        self.assertIsInstance(cm.exception, ValueError)        # editor.py ho ukáže jako chybu startu

    def test_version_matches_changelog_and_upgrading(self):
        root = Path(AD.__file__).resolve().parent
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        upgrading = (root / "UPGRADING.md").read_text(encoding="utf-8")
        self.assertRegex(AD.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(re.search(r"^## (\S+)", changelog, re.M).group(1), AD.VERSION)   # nejnovější verze nahoře
        self.assertIn("## %s" % AD.VERSION, upgrading)
        self.assertIn("contract = %d" % AD.CONTRACT, upgrading)

    def test_scripts_for(self):
        (self.tmp / "sitex_adapter.py").write_text(ADAPTER, encoding="utf-8")
        self.config('[editor]\nadapter = "sitex_adapter.py"\n')
        self.addCleanup(sys.modules.pop, "sitex_adapter", None)
        site = AD.load(self.tmp)
        self.assertIsNone(site.scripts_for({"gen/sablona.py": "x", "gen/a.py": "y"}))
        self.assertEqual(site.scripts_for({"gen/b.py": "y"}, also=["gen/a.py"]), ["gen/a.py", "gen/b.py"])
        self.assertEqual(site.generator_files(), ["gen/sablona.py", "gen/a.py", "gen/b.py"])


if __name__ == "__main__":
    unittest.main()
