# -*- coding: utf-8 -*-
"""Zápis hodnoty do textu JSON bez přeformátování zbytku (cms/jsonsource.py).

  python -m unittest discover -s cms/tests -t cms/tests -p "test_jsonsource.py"
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jsonsource as J   # noqa: E402

RAW = """
{
  "@type": "Person",
  "name": "Jan \\"J\\" Novák",
  "jobTitle": ["Forward Deployed Engineer", "E-commerce konzultant"],
  "address": {"@type": "PostalAddress","streetAddress": "Mírové náměstí 9/1","addressLocality": "Litoměřice"},
  "knowsAbout": [
    "Google Ads",
    "Sklik"
  ],
  "n": 1.5, "ok": true, "x": null, "e": [], "o": {}
}
"""


class JsonSourceTest(unittest.TestCase):
    def test_spans_cover_every_value(self):
        sp = J.spans(RAW)
        tree = json.loads(RAW)
        for path, (a, z) in sp.items():
            self.assertEqual(json.loads(RAW[a:z]), J.get(tree, path), path)
        self.assertIn(("knowsAbout", 1), sp)
        self.assertIn(("address", "addressLocality"), sp)

    def test_string_changes_only_that_value(self):
        out = J.replace(RAW, ("address", "addressLocality"), "Ústí nad Labem </script>")
        self.assertEqual(out, RAW.replace('"Litoměřice"', '"Ústí nad Labem <\\/script>"'))
        self.assertEqual(json.loads(out)["address"]["addressLocality"], "Ústí nad Labem </script>")

    def test_list_keeps_its_style(self):
        one = J.replace(RAW, ("jobTitle",), ["FDE", "Konzultant", "Vývojář webů"])
        self.assertIn('"jobTitle": ["FDE", "Konzultant", "Vývojář webů"],', one)
        lines = J.replace(RAW, ("knowsAbout",), ["Google Ads", "Sklik", "Shoptet"])
        self.assertIn('"knowsAbout": [\n    "Google Ads",\n    "Sklik",\n    "Shoptet"\n  ],', lines)
        self.assertIn('"e": ["a"]', J.replace(RAW, ("e",), ["a"]))
        self.assertIn('"knowsAbout": [],', J.replace(RAW, ("knowsAbout",), []))
        tight = '{"a": ["x","y"]}'
        self.assertEqual(J.replace(tight, ("a",), ["x", "z", "w"]), '{"a": ["x","z","w"]}')

    def test_sync_rewrites_only_differing_values(self):
        want = json.loads(RAW)
        want["knowsAbout"] = ["Google Ads", "Sklik"]
        self.assertEqual(J.sync(RAW, (), want), RAW)
        want["address"]["streetAddress"] = "Jiná 1"
        out = J.sync(RAW, (), want)
        self.assertEqual(out, RAW.replace("Mírové náměstí 9/1", "Jiná 1"))

    def test_sync_with_other_keys_rewrites_the_object(self):
        want = {"@type": "Person", "name": "X"}
        out = J.sync('{"a": {"@type": "Person"}}', ("a",), want)
        self.assertEqual(json.loads(out), {"a": want})

    def test_bad_json_and_missing_path(self):
        with self.assertRaises(J.JsonSourceError):
            J.spans('{"a": 1,}')
        with self.assertRaises(J.JsonSourceError):
            J.replace(RAW, ("nic",), "x")


def bs(s):
    """Zpětné lomítko jako ~ (escapy v textu JSON, které nástroje při zápisu souboru nerozbijí)."""
    return s.replace("~", chr(92))


NBSP, WJ = chr(0xA0), chr(0x2060)
DATA = bs('{\n  "a": [{"t": "Z~u00a0reportu do aplikace", "u": "/cesta"}, {"t": "Řekl ~"ano~""}],\n'
          '  "b": {"x": "Emoji ~ud83d~ude00 a tab~t", "n": 3}, "c": "Velké ~u00A0písmeno"\n}\n')


class StringsTest(unittest.TestCase):
    def test_strings_are_values_in_text_order(self):
        got = J.strings(DATA)
        self.assertEqual([p for p, _, _, _ in got], [("a", 0, "t"), ("a", 0, "u"), ("a", 1, "t"), ("b", "x"), ("c",)])
        p, a, z, v = got[0]
        self.assertEqual(v, "Z" + NBSP + "reportu do aplikace")
        self.assertEqual(DATA[a:z], bs('"Z~u00a0reportu do aplikace"'))
        self.assertEqual(got[3][3], "Emoji " + chr(0x1F600) + " a tab\t")

    def test_write_keeps_original_escapes_and_line(self):
        new = "Z" + NBSP + "reportu do finanční aplikace"
        out = J.write_string(DATA, ("a", 0, "t"), new)
        self.assertEqual(out, DATA.replace(bs("Z~u00a0reportu do aplikace"), bs("Z~u00a0reportu do finanční aplikace")))

    def test_new_invisible_chars_are_escaped_and_letters_literal(self):
        out = J.write_string(DATA, ("a", 1, "t"), 'Řekl "ne" v' + NBSP + "pátek" + WJ)
        self.assertIn(bs('"Řekl ~"ne~" v~u00a0pátek~u2060"'), out)
        out = J.write_string(DATA, ("b", "x"), "Emoji " + chr(0x1F600) + " a tab\t!")
        self.assertIn(bs('"Emoji ~ud83d~ude00 a tab~t!"'), out)
        out = J.write_string(DATA, ("c",), "Velké" + NBSP + "písmeno" + NBSP + "znovu")
        self.assertIn(bs('"Velké~u00A0písmeno~u00A0znovu"'), out)

    def test_write_refuses_non_string_and_missing_path(self):
        with self.assertRaises(J.JsonSourceError):
            J.write_string(DATA, ("b", "n"), "3")
        with self.assertRaises(J.JsonSourceError):
            J.write_string(DATA, ("b", "y"), "x")


class Merge3Test(unittest.TestCase):
    BASE = '{"a": {"t": "jedna", "u": "dva"}, "b": ["x", "y"]}\n'

    def test_values_on_one_line_merge_by_path_in_theirs_format(self):
        ours = self.BASE.replace('"jedna"', '"JEDNA"')
        theirs = self.BASE.replace('"dva"', '"DVA"').replace('["x", "y"]', '[\n  "x",\n  "y"\n]')
        out = J.merge3(ours, self.BASE, theirs)
        self.assertEqual(out, theirs.replace('"jedna"', '"JEDNA"'))
        self.assertEqual(json.loads(out), {"a": {"t": "JEDNA", "u": "DVA"}, "b": ["x", "y"]})

    def test_same_path_is_a_conflict_unless_equal(self):
        ours = self.BASE.replace('"jedna"', '"A"')
        self.assertIsNone(J.merge3(ours, self.BASE, self.BASE.replace('"jedna"', '"B"')))
        self.assertEqual(J.merge3(ours, self.BASE, ours), ours)
        self.assertIsNone(J.merge3(ours, self.BASE, self.BASE.replace('{"t": "jedna", "u": "dva"}', '{"t": "jedna"}')))
        self.assertIsNone(J.merge3(ours, self.BASE, "{"))


if __name__ == "__main__":
    unittest.main()
