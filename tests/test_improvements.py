from __future__ import annotations

import unittest

from document_parser import parse_questions, split_options
from excel_math import readable_math
from word_math import MATH_ATOM


class ParserTests(unittest.TestCase):
    def test_nested_option_references_remain_inside_d(self):
        _, options = split_options(
            "(A) Primary (B) Secondary (C) Share market (D) (B) and (C) both"
        )
        self.assertEqual(options["D"], "(B) and (C) both")

    def test_bilingual_mcq_pairing(self):
        pages = [{
            "source_page_index": 2,
            "blocks": [
                {"reading_order": 0, "label": "SectionHeader", "html": "Objective Type Questions"},
                {"reading_order": 1, "label": "Text", "html": "1. हिंदी प्रश्न?"},
                {"reading_order": 2, "label": "ListGroup", "html": "(A) एक (B) दो (C) तीन (D) चार"},
                {"reading_order": 3, "label": "Text", "html": "English question?"},
                {"reading_order": 4, "label": "ListGroup", "html": "(A) One (B) Two (C) Three (D) Four"},
            ],
        }]
        questions = parse_questions(pages)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].number, 1)
        self.assertEqual(questions[0].english, "English question?")
        self.assertEqual(questions[0].hindi, "हिंदी प्रश्न?")
        self.assertEqual(questions[0].option("D"), "Four")

    def test_excel_contains_no_latex_commands(self):
        value = readable_math(r"0.\overline{48}, \frac{p}{q}, q \neq 0, x^2")
        self.assertNotIn("\\", value)
        self.assertIn("p⁄q", value)
        self.assertIn("≠", value)
        self.assertIn("x²", value)

    def test_adjacent_variable_powers_are_separate_math_atoms(self):
        self.assertEqual(MATH_ATOM.findall("x^3y^2"), ["x^3", "y^2"])
        self.assertEqual(MATH_ATOM.findall("x^2y^3"), ["x^2", "y^3"])


if __name__ == "__main__":
    unittest.main()
