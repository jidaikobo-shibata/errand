import unittest

from errand.math import parse_formula
from errand.markdown import blocks


class MathTests(unittest.TestCase):
    def test_fraction_root_and_scripts(self):
        result = parse_formula(r'\frac{1+x^2}{\sqrt{5}}+a_{n}')
        self.assertEqual(result.children[0].kind, 'fraction')
        self.assertIn('割る（平方根（5））', result.readable())
        self.assertIn('下付き（n）', result.readable())
        self.assertIn('の（2）乗', result.readable())

    def test_basic_operators_and_box(self):
        result = parse_formula(r'(4\sqrt{5})^2=16\times5=\boxed{80}')
        self.assertIn('16×5', result.readable())
        self.assertEqual(result.children[-1].kind, 'box')

    def test_unsupported_or_incomplete_formula_is_rejected(self):
        for source in [r'\input{/etc/passwd}', r'\href{x}{y}', r'\begin{matrix}x\end{matrix}',
                       r'\frac{1}', r'\sqrt[3]{x}', r'x^{2', 'x^^2', '', '}']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_formula(source)

    def test_formula_limits(self):
        for source in ['x' * 2049, '{' * 30 + 'x' + '}' * 30, 'x' * 600]:
            with self.subTest(length=len(source)), self.assertRaises(ValueError):
                parse_formula(source)

    def test_display_and_inline_delimiters_preserve_surrounding_text(self):
        source = r'前\[\frac{1}{2}\]後 \(x^2\) と $a_1$。'
        parsed = blocks(source)
        self.assertEqual([b.text for b in parsed if b.kind == 'math'],
                         [r'\frac{1}{2}', 'x^2', 'a_1'])
        self.assertEqual(''.join(b.text for b in parsed if b.kind == 'text'), '前後  と 。')
        self.assertTrue(all(b.complete for b in parsed))

    def test_streaming_display_keeps_incomplete_math(self):
        parsed = blocks('説明\n$$\n\\frac{1}{')
        self.assertEqual(parsed[-1].kind, 'math')
        self.assertFalse(parsed[-1].complete)
        complete = blocks('説明\n$$\n\\frac{1}{2}\n$$')
        self.assertTrue(complete[-1].complete)

    def test_code_currency_and_tables_stay_intact(self):
        source = '価格は$10と$20。`\\(x^2\\)`\n```tex\n\\[x^2\\]\n```'
        self.assertFalse(any(b.kind == 'math' for b in blocks(source)))
        table = blocks('| 値 |\n| --- |\n| $x^2$ |\n')
        self.assertEqual(table[0].kind, 'table')
        self.assertEqual(table[0].rows[1], ('$x^2$',))
