# -*- coding: utf-8 -*-
"""Literály v Python zdrojích: index, f-stringy, zápis nové hodnoty a zpětné vyhodnocení.

  python -m unittest discover -s cms/tests -t cms/tests -p "test_pysource.py"
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pysource as P   # noqa: E402

SRC = (
    '# -*- coding: utf-8 -*-\n'
    'n, x = 1, "X"\n'
    'a = "Ahoj „světe“ \\"v uvozovkách\\" {ne pole}"\n'
    'b = f"Cena {n} Kč {{v závorce}} a {x!r:>3} konec"\n'
    "c = r'raw \\d+ text'\n"
    'd = """víc\n'
    'řádků"""\n'
    'e = ("část jedna " "část dva")\n'
    'f = b"bajty"\n'
    "g = 'slug-only/path.html'\n"
)


def run(src):
    ns = {}
    exec(compile(src, "<test>", "exec"), ns)
    return ns


class LiteralsTest(unittest.TestCase):
    def test_index_covers_strings_and_fstring_parts_but_not_bytes(self):
        vals = [lit.value for lit in P.literals(SRC)]
        self.assertIn('Ahoj „světe“ "v uvozovkách" {ne pole}', vals)
        self.assertIn("Cena ", vals)
        self.assertIn(" Kč {v závorce} a ", vals)
        self.assertIn(" konec", vals)
        self.assertIn("raw \\d+ text", vals)
        self.assertIn("víc\nřádků", vals)
        self.assertIn("část jedna ", vals)
        self.assertIn("část dva", vals)
        self.assertNotIn("bajty", vals)
        self.assertTrue(all(lit.fstr for lit in P.literals(SRC) if lit.value in ("Cena ", " konec")))

    def test_replace_keeps_source_valid_and_changes_only_that_value(self):
        before = run(SRC)
        for lit in P.literals(SRC):
            new = lit.value.replace("ě", "e") + " +\"nový\" {x}"
            if lit.raw:
                new = lit.value + " nový"
            after = run(P.replace(SRC, lit, new))
            for name in "abcdeg":
                if lit.value in str(before[name]) and before[name] != after[name]:
                    self.assertIn(new, str(after[name]))
                    break
            else:
                self.fail("hodnota %r se po zápisu nikde nezměnila" % lit.value)
            # zbytek zdroje je beze změny
            src2 = P.replace(SRC, lit, new)
            self.assertEqual(src2[:lit.a], SRC[:lit.a])
            self.assertEqual(src2[len(src2) - (len(SRC) - lit.z):], SRC[lit.z:])

    def test_fstring_braces_are_doubled(self):
        lit = next(lit for lit in P.literals(SRC) if lit.value == " konec")
        src2 = P.replace(SRC, lit, " konec {a} }")
        self.assertIn("{{a}} }}", src2)
        self.assertEqual(run(src2)["b"], "Cena 1 Kč {v závorce} a 'X' konec {a} }")

    def test_multiline_literal_keeps_crlf(self):
        src = SRC.replace("\n", "\r\n")
        lit = next(lit for lit in P.literals(src) if lit.value == "víc\nřádků")
        src2 = P.replace(src, lit, "víc\nnových\nřádků")
        self.assertEqual(src2.count("\n"), src2.count("\r\n"), "smíšené konce řádků")
        self.assertEqual(run(src2)["d"], "víc\nnových\nřádků")
        self.assertNotIn("\r", P.replace(SRC, next(x for x in P.literals(SRC) if x.q == '"""'), "a\nb"))

    def test_raw_literal_refuses_impossible_value(self):
        lit = next(lit for lit in P.literals(SRC) if lit.raw)
        with self.assertRaises(ValueError):
            P.encode(lit, "končí lomítkem\\")

    def test_ident_literals_are_not_text(self):
        self.assertTrue(P.IDENT.match("slug-only/path.html"))
        self.assertFalse(P.IDENT.match("Věta s mezerou"))


if __name__ == "__main__":
    unittest.main()
