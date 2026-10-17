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


if __name__ == "__main__":
    unittest.main()
