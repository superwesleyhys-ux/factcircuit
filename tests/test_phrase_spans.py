import hashlib
import unittest

from newsverify.phrase_spans import phrase_occurrences, select_items, text_sha256, validate_span


class ExactPhraseSpanTests(unittest.TestCase):
    def assert_exact_slices(self, content, item):
        self.assertEqual(content[item["start"]:item["end"]], item["text"])
        for field in ("before", "after", "context"):
            span = item[field]
            self.assertEqual(content[span["start"]:span["end"]], span["text"])
        self.assertEqual(item["before"]["text"] + item["text"] + item["after"]["text"],
                         item["context"]["text"])
        self.assertEqual(text_sha256(content), item["source_sha256"])

    def test_negation_outside_selected_phrase_is_retained_in_line_context(self):
        content = "Header\nThe report did not confirm that income was $100.6 million.\nFooter"
        item, = select_items(content, ["income was $100.6 million"], context_chars=0)
        self.assertEqual("income was $100.6 million", item["text"])
        self.assertIn("did not confirm", item["before"]["text"])
        self.assertTrue(item["clipped_left"])
        self.assertTrue(item["clipped_right"])
        self.assert_exact_slices(content, item)

    def test_multiline_table_keeps_literal_units_empty_cells_and_newlines(self):
        content = "All values USD millions; unaudited\nYear | Revenue | Note\n2021 | 100.6 | \n2020 | 20.0 | restated\n"
        item, = select_items(content, ["100.6"], context_chars=80)
        self.assertIn("USD millions; unaudited", item["before"]["text"])
        self.assertIn("2021 | 100.6 | \n", item["context"]["text"])
        self.assertFalse(item["clipped_left"])
        self.assertFalse(item["clipped_right"])
        self.assert_exact_slices(content, item)

    def test_short_context_does_not_claim_distant_table_header_is_present(self):
        content = "Units: millions\n" + "row | value\n" * 50 + "Final | 100.6 | pending\nEnd\n"
        item, = select_items(content, ["100.6"], context_chars=0)
        self.assertNotIn("Units", item["context"]["text"])
        self.assertTrue(item["clipped_left"])
        self.assertTrue(item["clipped_right"])
        self.assertEqual("Final | 100.6 | pending\n", item["context"]["text"])

    def test_chinese_individual_character_and_phrase_preserve_exact_offsets(self):
        content = "该声明尚未证实；公司声称收入增长。"
        items = select_items(content, ["收入增长", "未"])
        self.assertEqual(["未", "收入增长"], [item["text"] for item in items])
        self.assertEqual(content.index("未"), items[0]["start"])
        for item in items:
            self.assert_exact_slices(content, item)

    def test_emoji_and_combining_marks_use_codepoints_without_normalization(self):
        content = "🧪 cafe\u0301 ≠ café 👩‍🔬"
        items = select_items(content, ["cafe\u0301", "👩‍🔬", {"start": 0, "end": 1}])
        self.assertEqual(["🧪", "cafe\u0301", "👩‍🔬"], [item["text"] for item in items])
        self.assertEqual(1, items[0]["end"])
        self.assertEqual(3, items[2]["end"] - items[2]["start"])
        self.assertNotEqual(text_sha256("cafe\u0301"), text_sha256("café"))
        with self.assertRaisesRegex(ValueError, "does not occur exactly"):
            select_items("cafe\u0301", ["café"])

    def test_line_ending_and_whitespace_are_not_rewritten(self):
        content = "No\tchange\r\n  Revenue:  100.6 \r\n"
        item, = select_items(content, ["  100.6 "], context_chars=100)
        self.assertEqual(content, item["context"]["text"])
        self.assert_exact_slices(content, item)
        spaces = select_items("a  b", ["  "])
        self.assertEqual("  ", spaces[0]["text"])

    def test_selection_ending_at_newline_does_not_consume_the_next_line(self):
        content = "Header\nFirst row\nSecond row\nFooter\n"
        item, = select_items(content, ["row\nSecond row\n"], context_chars=0)
        self.assertEqual("First row\nSecond row\n", item["context"]["text"])
        self.assertNotIn("Footer", item["after"]["text"])
        self.assertTrue(item["clipped_right"])
        self.assert_exact_slices(content, item)

    def test_repeated_overlapping_occurrences_are_all_retained(self):
        items = select_items("banana banana", ["ana"])
        self.assertEqual([(1, 4), (3, 6), (8, 11), (10, 13)],
                         [(item["start"], item["end"]) for item in items])
        self.assertEqual(4, len({item["id"] for item in items}))

    def test_explicit_spans_and_selector_order_have_stable_identity(self):
        content = "beta alpha beta"
        first = select_items(content, ["beta", {"start": 5, "end": 10}])
        second = select_items(content, ["alpha", "beta"])
        self.assertEqual(first, second)
        changed = select_items(content + ".", ["alpha", "beta"])
        self.assertNotEqual(first[0]["id"], changed[0]["id"])
        self.assertEqual(first[0]["id"], select_items(content, ["beta"], context_chars=0)[0]["id"])

    def test_duplicate_spans_fail_instead_of_counting_twice(self):
        for selectors in (["beta", "beta"], ["beta", {"start": 0, "end": 4}]):
            with self.subTest(selectors=selectors), self.assertRaisesRegex(ValueError, "duplicate span"):
                select_items("beta alpha", selectors)

    def test_selection_budget_fails_instead_of_truncating(self):
        self.assertEqual(100, len(select_items("x" * 100, ["x"])))
        with self.assertRaisesRegex(ValueError, "100 item limit"):
            select_items("x" * 101, ["x"])
        with self.assertRaisesRegex(ValueError, "100 item limit"):
            select_items("x" * 101, [{"start": i, "end": i + 1} for i in range(101)])
        with self.assertRaisesRegex(ValueError, "100 item limit"):
            select_items("x" * 50 + "y" * 51, ["x", "y"])

    def test_invalid_indices_cannot_use_python_negative_boolean_or_float_slicing(self):
        for start, end in ((-1, 2), (0, 4), (2, 1), (1, 1), (False, 1),
                           (0, True), (0.0, 1), (0, 1.0), ("0", 1), (0, None)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                select_items("abc", [{"start": start, "end": end}])

    def test_malformed_selectors_fail_closed(self):
        for selectors in (None, [], (), "abc", [""], [42],
                          [{"start": 0}], [{"start": 0, "end": 1, "quote": "a"}], ["A"]):
            with self.subTest(selectors=selectors), self.assertRaises(ValueError):
                select_items("abc", selectors)

    def test_validate_span_rejects_forged_quote_and_wrong_occurrence_offsets(self):
        content = "First 100.6, revised 90.2."
        start = content.index("100.6")
        self.assertIsNone(validate_span(content, start, start + 5, "100.6"))
        for quote in ("90.2", "100.6 ", "100,6", None):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                validate_span(content, start, start + 5, quote)
        with self.assertRaises(ValueError):
            validate_span(content, 0, 5, "100.6")
        with self.assertRaises(ValueError):
            validate_span(content, False, 5, "First")

    def test_occurrence_index_is_exact_source_bound_and_allows_no_match(self):
        content = "bananana"
        occurrences = phrase_occurrences("source:1", content, "ana", context_chars=0)
        self.assertEqual([1, 3, 5], [row["start"] for row in occurrences])
        self.assertEqual(occurrences, phrase_occurrences("source:1", content, "ana", context_chars=0))
        self.assertEqual([], phrase_occurrences("source:1", content, "ANA"))
        self.assertEqual([], phrase_occurrences("source:1", "", "ana"))
        other_source = phrase_occurrences("source:2", content, "ana", context_chars=0)
        self.assertNotEqual(occurrences[0]["id"], other_source[0]["id"])
        self.assertEqual(occurrences[0]["source_sha256"], other_source[0]["source_sha256"])
        for row in occurrences:
            self.assertEqual("source:1", row["source_id"])
            self.assert_exact_slices(content, row)

    def test_occurrence_index_also_fails_for_over_budget_invalid_phrases_or_sources(self):
        with self.assertRaisesRegex(ValueError, "100 item limit"):
            phrase_occurrences("source", "a" * 101, "a")
        for source in (None, "", "   ", 4):
            with self.subTest(source=source), self.assertRaises(ValueError):
                phrase_occurrences(source, "text", "text")
        for phrase in (None, "", 4):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                phrase_occurrences("source", "text", phrase)

    def test_hash_is_utf8_exact_and_invalid_text_is_rejected(self):
        self.assertEqual(hashlib.sha256("行\r\n🧪".encode("utf-8")).hexdigest(), text_sha256("行\r\n🧪"))
        self.assertNotEqual(text_sha256("a\r\n"), text_sha256("a\n"))
        for invalid in (None, b"bytes", "\ud800"):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                text_sha256(invalid)

    def test_invalid_context_budget_is_rejected_by_both_public_extractors(self):
        for context_chars in (-1, True, 1.5, None):
            with self.subTest(context_chars=context_chars), self.assertRaises(ValueError):
                select_items("text", ["text"], context_chars=context_chars)
            with self.subTest(context_chars=context_chars), self.assertRaises(ValueError):
                phrase_occurrences("source", "text", "text", context_chars=context_chars)


if __name__ == "__main__":
    unittest.main()
