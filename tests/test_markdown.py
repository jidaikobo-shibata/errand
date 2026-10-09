import unittest
from xml.etree import ElementTree

from errand.markdown import blocks, link_target, render


def parsed(source):
    return ElementTree.fromstring("<root>" + render(source) + "</root>")


class MarkdownTests(unittest.TestCase):
    def test_quote_groups_lines_and_preserves_paragraphs(self):
        result = blocks("前文\n> **引用**\n> 続き\n>\n> 次の段落\n\n後文")
        self.assertEqual([part.kind for part in result], ["text", "quote", "text"])
        self.assertEqual(result[1].text, "**引用**\n続き\n\n次の段落\n")

    def test_quote_contains_nested_quotes_and_literal_code(self):
        quote = blocks("> 外側\n> > 内側\n> ```text\n> > コード内では引用ではない\n> ```")[0]
        children = blocks(quote.text)
        self.assertEqual([part.kind for part in children], ["text", "quote", "code"])
        self.assertEqual(children[1].text, "内側\n")
        self.assertEqual(children[2].text, "> コード内では引用ではない\n")

    def test_separate_code_blocks_preserve_their_own_content(self):
        result = blocks("前文\n```bash\n  echo '<b>&</b>'\n\n```\n中間\n~~~text\nsecond\n~~~\n後文")
        self.assertEqual([part.kind for part in result], ["text", "code", "text", "code", "text"])
        self.assertEqual(result[1].text, "  echo '<b>&</b>'\n\n")
        self.assertEqual(result[1].language, "bash")
        self.assertEqual(result[3].text, "second\n")
        self.assertTrue(result[1].complete and result[3].complete)

    def test_code_block_streaming_and_fence_lengths(self):
        part = blocks("````python\nprint(1)\n```\nunfinished")[0]
        self.assertFalse(part.complete)
        self.assertEqual(part.text, "print(1)\n```\nunfinished")
        part = blocks("````python\nprint(1)\n```\nunfinished\n````")[0]
        self.assertTrue(part.complete)
        self.assertEqual(part.text, "print(1)\n```\nunfinished\n")

    def test_empty_code_and_windows_line_endings(self):
        self.assertEqual(blocks("```\n```")[0].text, "")
        self.assertEqual(blocks("```text\r\na\r\n\r\n```\r\n")[0].text, "a\r\n\r\n")

    def test_table_rows_alignment_and_surrounding_prose(self):
        result = blocks("前文\n\n| 名前 | 数 | 中央 |\n| :--- | ---: | :---: |\n| **項目** | 12 | 値 |\n\n後文")
        self.assertEqual([part.kind for part in result], ["text", "table", "text"])
        self.assertEqual(result[1].rows, (("名前", "数", "中央"), ("**項目**", "12", "値")))
        self.assertEqual(result[1].alignments, ("left", "right", "center"))

    def test_table_escaped_pipes_missing_cells_and_code_fences(self):
        result = blocks("| A | B |\n| --- | --- |\n| a\\|b | `x|y` |\n| one |\n| a | b | extra |")
        self.assertEqual(result[0].rows[1], ("a|b", "`x|y`"))
        self.assertEqual(result[0].rows[2], ("one", ""))
        self.assertEqual(result[0].rows[3], ("a", "b"))
        self.assertEqual(blocks("```\n| A | B |\n| --- | --- |\n``` ")[0].kind, "code")
        self.assertEqual(blocks("| A | B |\n| -- | -- |\n")[0].kind, "text")

    def test_headings_lists_and_emphasis(self):
        root = parsed("# 題名\n\n- **太字**と*斜体*\n  - 子項目\n1. `code`\n> 引用\n~~削除~~")
        self.assertEqual(root.find("span").get("weight"), "bold")
        text = "".join(root.itertext())
        self.assertIn("• 太字と斜体", text)
        self.assertIn("  • 子項目", text)
        self.assertIn("1. code", text)
        self.assertIn("│ 引用", text)
        self.assertEqual(root.find("b").text, "太字")
        self.assertEqual(root.find("i").text, "斜体")
        self.assertEqual(root.find("s").text, "削除")

    def test_code_preserves_whitespace_and_special_characters(self):
        source = "```bash\n  echo '<b>&</b>'\n**literal**\n```"
        root = parsed(source)
        self.assertEqual("".join(root.itertext()), "  echo '<b>&</b>'\n**literal**")
        self.assertIsNone(root.find("b"))
        self.assertEqual(root.find("span").get("font_family"), "monospace")

    def test_streamed_unclosed_code_and_delimiters(self):
        for source in ["**未完", "[リンク](", "```python\nprint('<&')", "# 見出", "<script>"]:
            with self.subTest(source=source):
                parsed(source)
        root = parsed("```\nfirst\n``\nsecond\n```")
        self.assertEqual("".join(root.itertext()), "first\n``\nsecond")

    def test_html_and_pango_are_literal_text(self):
        source = '<span size="999999">悪意</span> <script>alert(1)</script> &'
        root = parsed(source)
        self.assertEqual("".join(root.itertext()), source)
        self.assertEqual(len(root), 0)

    def test_links_escape_labels_and_attributes(self):
        root = parsed('[**公式**](https://example.com/?a=1&b=2)\n[資料](/tmp/a b.pdf)')
        link = root.find("a")
        self.assertEqual(link.get("href"), "https://example.com/?a=1&b=2")
        self.assertEqual(link.find("b").text, "公式")
        root = parsed('[資料](</tmp/a b.pdf>)')
        self.assertEqual(root.find("a").get("href"), "file:///tmp/a%20b.pdf")

    def test_dangerous_schemes_and_remote_files_are_not_links(self):
        for target in ["javascript:alert", "data:text/html,hello", "file://remote/tmp/a", "ftp://example.com/a", "relative/file"]:
            with self.subTest(target=target):
                self.assertIsNone(link_target(target))
                self.assertIsNone(parsed(f"[link]({target})").find("a"))

    def test_images_do_not_load_and_code_does_not_link(self):
        root = parsed("![画像](https://example.com/image.png)\n`[リンク](https://example.com)`")
        self.assertIsNone(root.find("a"))
        self.assertIn("画像", "".join(root.itertext()))

    def test_backslash_escapes_and_identifiers(self):
        root = parsed(r"\*literal\* and some_variable_name")
        self.assertEqual("".join(root.itertext()), "*literal* and some_variable_name")
        self.assertIsNone(root.find("i"))

    def test_setext_heading_and_horizontal_rule(self):
        root = parsed("題名\n===\n\n---")
        self.assertEqual(root.find("span").text, "題名")
        self.assertIn("────────────", "".join(root.itertext()))


if __name__ == "__main__":
    unittest.main()
