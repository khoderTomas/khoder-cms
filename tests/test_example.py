# -*- coding: utf-8 -*-
"""Ukázkový web (example/) jako fixtura motoru: build a kontrola webu, nastavení pro deploy, pokrytí
textů a úpravy přes editor (apply.Editor) s adaptérem ukázky, vždy nad kopií v tempu.

  python -m unittest discover -s tests -t tests -p "test_example.py"
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CMS = Path(__file__).resolve().parents[1]
EXAMPLE = CMS / "example"
sys.path.insert(0, str(CMS))
import adapter as AD    # noqa: E402
import apply as A       # noqa: E402
import blocks as B      # noqa: E402
import classify as C    # noqa: E402
import coverage as CV   # noqa: E402
import deploy as D      # noqa: E402
import htmltext as H    # noqa: E402
import replace as R     # noqa: E402

AD.load(EXAMPLE)        # modul adaptéru (a build/web.py) z repa, kopie v tempu ho sdílí
import web as W         # noqa: E402

HEADER, FOOTER = "content/partials/header.html", "content/partials/footer.html"


def copy_example():
    tmp = Path(tempfile.mkdtemp(prefix="cms-example-"))
    shutil.copytree(EXAMPLE, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".cms"))
    return tmp


def run(tmp, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable] + list(args), cwd=tmp, env=env, capture_output=True, text=True,
                          encoding="utf-8")


def snapshot(root):
    files = list((root / "www").glob("*.html")) + list((root / "content").rglob("*.*"))
    return {p: p.read_bytes() for p in files}


def after_html(src, block, fn):
    """innerHTML bloku, jak by ho poslal prohlížeč: anotovaný inner upravený funkcí fn."""
    ann = B.annotate(src)
    ab = next(b for b in B.blocks(ann) if b.n == block.n)
    return fn(ab.inner(ann))


class BuildTest(unittest.TestCase):
    """build/build.py a build/sitemap.py nad kopií: ukázka v repu je sestavená a build ji nezmění."""

    def setUp(self):
        self.tmp = copy_example()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_build_is_idempotent_and_verify_passes(self):
        before = snapshot(self.tmp)
        r = run(self.tmp, "build/build.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("build: 6 stránek, 0 souborů aktualizováno", r.stdout)
        self.assertIn("verify: 6 stránek | 0 chyb", r.stdout)
        self.assertEqual(snapshot(self.tmp), before)

    def test_sitemap_is_current_and_skips_noindex(self):
        before = (self.tmp / "www" / "sitemap.xml").read_bytes()
        r = run(self.tmp, "build/sitemap.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "www" / "sitemap.xml").read_bytes(), before)
        self.assertNotIn("/404", before.decode("utf-8"))
        self.assertIn("<loc>https://www.example.com/</loc>", before.decode("utf-8"))

    def test_build_fills_partials_entity_and_atoms(self):
        page = self.tmp / "www" / "kontakt.html"
        s = page.read_text(encoding="utf-8")
        s = re.sub(r"(<!-- PARTIAL:header -->\n).*?(<!-- /PARTIAL:header -->\n)", r"\1\2", s, flags=re.S)
        s = s.replace(">+420 123 456 789<", ">starý telefon<")
        page.write_text(s, encoding="utf-8", newline="")
        site = json.loads((self.tmp / "content" / "site.json").read_text(encoding="utf-8"))
        site["business"]["telephone"] = "+420987654321"
        (self.tmp / "content" / "site.json").write_text(W.entity_text(site), encoding="utf-8", newline="")
        r = run(self.tmp, "build/build.py", "--verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("kontakt.html: hlavička a patička (content/partials/) neodpovídá datům", r.stdout)
        self.assertIn("kontakt.html: údaje o firmě v JSON-LD (content/site.json) neodpovídá datům", r.stdout)
        self.assertIn("index.html: odkaz tel:+420123456789 nesedí na telefon", r.stdout)
        r = run(self.tmp, "build/build.py")
        self.assertEqual(r.returncode, 0, r.stdout)
        out = page.read_text(encoding="utf-8")
        self.assertIn('<a href="/kontakt" aria-current="page">Kontakt</a>', out)
        self.assertIn('<a data-site="telephone" href="tel:+420987654321">+420 987 654 321</a>', out)
        self.assertIn('"telephone": "+420987654321"', out)


class CheckTest(unittest.TestCase):
    """Kontroly stránky (build/web.py check), které build i editor pouští nad každou stránkou."""

    def setUp(self):
        self.data = W.Data(EXAMPLE)
        self.src = self.data.page("sluzby")

    def fails(self, fn, slug="sluzby"):
        return W.check(slug, fn(self.data.page(slug)), self.data)

    def test_example_pages_pass(self):
        self.assertEqual(W.verify(self.data), [])

    def test_seo_rules(self):
        cases = [
            (lambda s: s.replace("<title>", "<title>" + "x" * 80), "title má"),
            (lambda s: s.replace('<link rel="canonical" href="https://www.example.com/sluzby">', ""), "canonical má být"),
            (lambda s: s.replace("<h2>Kuchyně na míru</h2>", "<h1>Kuchyně na míru</h1>"), "má 2× h1"),
            (lambda s: s.replace("</main>", '<details class="faq-item"><summary>Navíc?</summary>'
                                 '<div class="faq-body"><p>Ano.</p></div></details></main>'),
             "FAQPage v JSON-LD má 3 otázek, na stránce je jich 4"),
            (lambda s: s.replace(' width="1200"', "", 1), "obrázek bez width"),
            (lambda s: s.replace("</main>", '<a href="/neni-stranka">x</a></main>'), "odkaz /neni-stranka nevede"),
            (lambda s: s.replace("</main>", '<a href="mailto:jiny@example.com">x</a></main>'), "nesedí na e-mail"),
            (lambda s: s.replace("<title>Co vyrábíme: kuchyně, skříně a renovace | Truhlářství Habr</title>",
                                 "<title>Kontakt a poptávka | Truhlářství Habr</title>"), "stejný title jako kontakt.html"),
            (lambda s: s.replace("\n", "\r\n"), "konce řádků CRLF"),
        ]
        for fn, want in cases:
            got = self.fails(fn)
            self.assertTrue(any(want in x for x in got), (want, got))

    def test_noindex_page_needs_no_canonical(self):
        self.assertEqual(self.fails(lambda s: s, "404"), [])
        self.assertTrue(self.fails(lambda s: s.replace('<meta name="robots" content="noindex">\n', ""), "404"))

    def test_partial_slots_and_templates(self):
        got = self.fails(lambda s: s.replace("<!-- PARTIAL:footer -->\n", "", 1))
        self.assertTrue(any("značky PARTIAL nejsou v párech" in x for x in got), got)
        got = self.fails(lambda s: s.replace("Co vyrábíme</a>", "Výrobky</a>", 1))
        self.assertTrue(any("hlavička a patička" in x for x in got), got)
        bad = W.Data(EXAMPLE, {HEADER: self.data.text(HEADER).replace('href="/sluzby"', 'href="/sluzby" aria-current="page"')})
        self.assertTrue(any("aria-current počítá build" in x for x in W.verify(bad)))

    def test_typography(self):
        self.assertEqual(W.typo_html('<p>Dílna v Praze a v <a href="/a v b">80 km</a></p>'),
                         '<p>Dílna v&nbsp;Praze a&nbsp;v&nbsp;<a href="/a v b">80&nbsp;km</a></p>')
        once = W.typo_html("a i v dílně")
        self.assertEqual(W.typo_html(once), once)


class DeployConfigTest(unittest.TestCase):
    """cms.toml ukázky projde kontrolami deploye nad commitem: každé HTML ve www/ je v manifestu."""

    def test_commit_passes_deploy_checks(self):
        if not shutil.which("git"):
            self.skipTest("bez gitu")
        tmp = copy_example()
        self.addCleanup(shutil.rmtree, tmp, True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "ukázka"]):
            subprocess.run(["git"] + cmd, cwd=tmp, env=env, check=True, capture_output=True)
        repo = D.Repo(tmp)
        cfg = D.load_config((tmp / "cms.toml").read_text(encoding="utf-8"))
        sha = repo.rev("HEAD")
        pages = D.manifest_pages(repo, sha, cfg)
        self.assertEqual(D.check(D.export(repo, sha, cfg), cfg, pages), [])
        self.assertEqual(len(pages), 6)


class CoverageTest(unittest.TestCase):
    def test_example_is_editable_by_click(self):
        sm = CV.summary(*CV.collect(EXAMPLE, jobs=2))
        v = sm["visible"]
        self.assertEqual(sm["pages"], 6)
        self.assertEqual(v["locked"], [])                         # žádný viditelný text zamčený
        self.assertGreater(v["state"]["klik"]["share"], 90)
        self.assertGreater(v["state"]["pocitany"]["chars"], 0)    # údaje data-site počítá build
        self.assertEqual(sm["scripts"], [])                       # texty nejsou v JS
        self.assertNotIn("js", sm["other"])


class EditorTest(unittest.TestCase):
    """Úpravy přes editor nad kopií ukázky: zapíše se jen zdroj textu, build v paměti a kontrola
    projdou a Vrátit vrátí bajty."""

    def setUp(self):
        self.tmp = copy_example()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ed = A.Editor(self.tmp, journal_dir=self.tmp / ".cms" / "journal")
        self.before = snapshot(self.tmp)

    def block(self, slug, pred):
        src = self.ed.read_page(slug)
        return src, next(b for b in B.blocks(src) if pred(H.norm(B.block_text(src, b)), b))

    def save(self, slug, src, b, fn, **kw):
        return self.ed.save_block(slug, b.n, b.hash(src), B.page_hash(src), after_html(src, b, fn), **kw)

    def assert_pages_pass(self):
        for slug in sorted(self.ed.registry()):
            self.assertEqual(self.ed.check(slug, self.ed.read_page(slug)), [], slug)

    def test_paragraph_gets_typography_and_undo_restores(self):
        src, b = self.block("index", lambda t, b: t.startswith("Přijedeme, změříme"))
        r = self.save("index", src, b, lambda s: s.replace("Zaměření v&nbsp;Praze je zdarma.",
                                                           "Zaměření v Praze a v okolí je zdarma."))
        self.assertTrue(r["ok"], r)
        page = self.ed.read_page("index")
        self.assertIn("Zaměření v&nbsp;Praze a&nbsp;v&nbsp;okolí je zdarma.", page)
        self.assertEqual(page.replace(B.block_text(page, next(x for x in B.blocks(page) if x.n == b.n)), ""),
                         src.replace(B.block_text(src, b), ""))
        self.assert_pages_pass()
        self.ed.undo(r["entry"])
        self.assertEqual(snapshot(self.tmp), self.before)

    def test_atom_is_computed_and_not_a_block(self):
        src = self.ed.read_page("kontakt")
        items = C.classify(C.page(self.ed, "kontakt"))
        self.assertIn(("+420 123 456 789", C.POCITANY), [(i.text, i.state) for i in items if i.kind == "text"])
        self.assertEqual([b.n for b in B.blocks(src) if "data-site" in src[b.node.s:b.node.e]], [])

    def test_faq_question_updates_jsonld(self):
        src, b = self.block("sluzby", lambda t, b: t == "Jak dlouho výroba trvá?")
        r = self.save("sluzby", src, b, lambda s: s.replace("Jak dlouho výroba trvá?", "Jak dlouho trvá výroba?"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["linked"], 1)
        page = self.ed.read_page("sluzby")
        self.assertIn('"name": "Jak dlouho trvá výroba?"', page)
        self.assert_pages_pass()

    def test_menu_item_goes_to_template_and_all_pages(self):
        src, b = self.block("o-dilne", lambda t, b: b.part == HEADER and t == "O dílně")
        self.assertIn('aria-current="page"', src[b.node.s:b.node.se])
        new = lambda s: s.replace("O dílně", "Naše dílna")
        c = self.save("o-dilne", src, b, new)
        self.assertEqual((c["part"], c["before"], c["after"]), (HEADER, "O dílně", "Naše dílna"))
        self.assertEqual((len(c["pages"]), c["owners"]), (6, ["ochrana-osobnich-udaju"]))
        with self.assertRaises(A.Fail) as cm:               # owner stránka jen se zaškrtnutím
            self.save("o-dilne", src, b, new, confirm=c["confirm"])
        self.assertEqual(cm.exception.code, "owners")
        d = self.save("o-dilne", src, b, new, confirm=c["confirm"], owners=True)
        self.assertTrue(d["ok"], d)
        tpl = (self.tmp / HEADER).read_text(encoding="utf-8")
        self.assertIn('<a href="/o-dilne">Naše dílna</a>', tpl)
        self.assertIn('<a href="/o-dilne" aria-current="page">Naše dílna</a>', self.ed.read_page("o-dilne"))
        self.assertIn('<a href="/o-dilne">Naše dílna</a>', self.ed.read_page("ochrana-osobnich-udaju"))
        self.assert_pages_pass()
        self.ed.undo(d["entry"])
        self.assertEqual(snapshot(self.tmp), self.before)

    def test_owner_page_needs_unlock(self):
        src, b = self.block("ochrana-osobnich-udaju", lambda t, b: t.startswith("Web nepoužívá cookies"))
        fn = lambda s: s.replace("cookies", "sušenky")
        with self.assertRaises(A.Fail) as cm:
            self.save("ochrana-osobnich-udaju", src, b, fn)
        self.assertEqual(cm.exception.code, "locked")
        self.ed.unlock("ochrana-osobnich-udaju")
        self.assertTrue(self.save("ochrana-osobnich-udaju", src, b, fn)["ok"])

    def test_seo_title_and_duplicate_is_refused(self):
        cur = self.ed.read_page("sluzby")
        old = W.title_of(cur)
        r = self.ed.save_seo("sluzby", [{"id": "title", "old": old, "new": "Nábytek na míru | Truhlářství Habr"}])
        self.assertTrue(r["ok"], r)
        self.assertIn("<title>Nábytek na míru | Truhlářství Habr</title>", self.ed.read_page("sluzby"))
        with self.assertRaises(A.Fail) as cm:
            self.ed.save_seo("sluzby", [{"id": "title", "old": "Nábytek na míru | Truhlářství Habr",
                                         "new": "Kontakt a poptávka | Truhlářství Habr"}])
        self.assertEqual(cm.exception.code, "verify")
        self.assertTrue(any("stejný title" in x for x in cm.exception.details), cm.exception.details)

    def test_seo_panel_offers_service_texts(self):
        pan = self.ed.seo_panel("sluzby")
        labels = {r["label"] for o in pan["ld"] for r in o["rows"] if r["edit"]}
        self.assertIn("Název služby", labels)
        self.assertEqual(len(pan["faq"]), 3)
        self.assertIsNone(pan["person"])                    # služby údaje o firmě nenesou
        self.assertIsNotNone(self.ed.seo_panel("kontakt")["person"])

    def test_entity_address_rebuilds_carriers_and_atoms(self):
        changes = [{"id": "p:address.streetAddress", "old": "Ukázková 12", "new": "Dlouhá 3"}]
        c = self.ed.save_seo("kontakt", changes)
        self.assertIn("confirm", c, c)
        self.assertEqual([p["slug"] for p in c["pages"]], ["index", "kontakt", "ochrana-osobnich-udaju"])
        d = self.ed.save_seo("kontakt", changes, confirm=c["confirm"], owners=True)
        self.assertTrue(d["ok"], d)
        site = json.loads((self.tmp / "content" / "site.json").read_text(encoding="utf-8"))
        self.assertEqual(site["business"]["address"]["streetAddress"], "Dlouhá 3")
        self.assertIn('<p data-site="address">Dlouhá 3, 100 00 Praha</p>', self.ed.read_page("kontakt"))
        self.assertIn('"streetAddress": "Dlouhá 3"', self.ed.read_page("index"))
        self.assert_pages_pass()
        self.ed.undo(d["entry"])
        self.assertEqual(snapshot(self.tmp), self.before)

    def test_alt_text(self):
        src = self.ed.read_page("index")
        x = next(n for n in self.ed.attr_nodes("index", src) if n.state == C.PANEL and "alt" in n.names)
        r = self.ed.save_attrs("index", [{"i": x.i, "a": "alt", "old": C.norm(x.node.attrs["alt"]),
                                          "new": "Kuchyně z dubové dýhy"}])
        self.assertTrue(r["ok"], r)
        self.assertIn('alt="Kuchyně z dubové dýhy"', self.ed.read_page("index"))

    def test_find_and_replace_in_template_and_page(self):
        q, rep = "Poptat zakázku", "Nezávazná poptávka"
        r = R.find(self.ed, q)
        paths = sorted(f["path"] for f in r["files"])
        self.assertEqual(paths, [HEADER, "www/index.html"])
        c = R.replace(self.ed, q, rep, paths, r["sig"])
        self.assertIn("confirm", c, c)
        d = R.replace(self.ed, q, rep, paths, r["sig"], c["confirm"], owners=bool(c["owners"]))
        self.assertTrue(d.get("ok"), d)
        for slug in self.ed.registry():
            self.assertNotIn(q, self.ed.read_page(slug), slug)
        self.assert_pages_pass()

    def test_find_skips_entity_block_and_atoms(self):
        r = R.find(self.ed, "Ukázková 12")
        self.assertEqual(r["total"], 0, r["files"])


if __name__ == "__main__":
    unittest.main()
