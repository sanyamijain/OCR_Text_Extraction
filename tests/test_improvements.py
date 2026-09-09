from __future__ import annotations

import sys
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from PIL import Image

from document_parser import parse_questions, split_options
from excel_math import readable_math
from run_surya import (
    PageStalledError,
    deduplicate_overlap_blocks,
    recognize_split_adaptive,
    recognize_with_deadline,
    windows_safe_stop_process,
)
from structured_output import html_logical_lines
from word_math import MATH_ATOM


class ImprovementTests(unittest.TestCase):
    def test_failed_region_is_subdivided_without_losing_other_regions(self):
        class Result:
            def model_dump(self):
                return {"blocks": [{"label": "Text", "html": "ok", "bbox": [0, 1, 10, 10]}]}

        class Predictor:
            calls = 0

            def __call__(self, images, full_page=True):
                Predictor.calls += 1
                if Predictor.calls == 1:
                    raise ConnectionError("simulated backend loss")
                return [Result()]

        predictor = Predictor()
        with patch("run_surya.load_pdf", return_value=([Image.new("RGB", (200, 800), "white")], None)), patch(
            "run_surya.RecognitionPredictor", return_value=predictor
        ):
            data, returned = recognize_split_adaptive(
                predictor, object(), 0, split_parts=4, region_timeout=5,
                region_tokens=512, max_depth=1
            )
        self.assertIs(returned, predictor)
        self.assertGreaterEqual(Predictor.calls, 6)
        self.assertTrue(data["blocks"])

    def test_windows_backend_stop_verifies_pid_then_kills_tree(self):
        tasklist = subprocess.CompletedProcess([], 0, '"llama-server.exe","24208"\n', "")
        taskkill = subprocess.CompletedProcess([], 0, "SUCCESS", "")
        with patch("run_surya.subprocess.run", side_effect=[tasklist, taskkill]) as run:
            windows_safe_stop_process(24208, "llamacpp")
        self.assertEqual(run.call_args_list[1].args[0], ["taskkill", "/PID", "24208", "/T", "/F"])

    def test_hard_watchdog_marks_stalled_page(self):
        with patch("run_surya.recognize_page", side_effect=lambda *args: time.sleep(0.2)), patch(
            "run_surya.stop_stalled_local_backend"
        ) as stop_backend:
            with self.assertRaises(PageStalledError):
                recognize_with_deadline(object(), object(), 0, False, 3, 0.01)
        stop_backend.assert_called_once()

    def test_split_overlap_deduplicates_equivalent_html_wrappers(self):
        blocks = [
            {"label": "Text", "html": "(C) <math>x^2</math>", "bbox": [0, 100, 50, 120]},
            {"label": "Equation", "html": "<p>(C) <math>x^2</math></p>", "bbox": [0, 125, 50, 145]},
            {"label": "Text", "html": "(D) 0", "bbox": [0, 150, 50, 170]},
        ]
        result = deduplicate_overlap_blocks(blocks, overlap=48)
        self.assertEqual(len(result), 2)

    def test_nested_option_references_remain_inside_d(self):
        _, options = split_options("(A) Primary (B) Secondary (C) Share market (D) (B) and (C) both")
        self.assertEqual(options["D"], "(B) and (C) both")

    def test_bilingual_mcq_pairing(self):
        pages = [{"source_page_index": 2, "blocks": [
            {"reading_order": 0, "label": "SectionHeader", "html": "Objective Type Questions"},
            {"reading_order": 1, "label": "Text", "html": "1. हिंदी प्रश्न?"},
            {"reading_order": 2, "label": "ListGroup", "html": "(A) एक (B) दो (C) तीन (D) चार"},
            {"reading_order": 3, "label": "Text", "html": "English question?"},
            {"reading_order": 4, "label": "ListGroup", "html": "(A) One (B) Two (C) Three (D) Four"},
        ]}]
        questions = parse_questions(pages)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].english, "English question?")
        self.assertEqual(questions[0].hindi, "हिंदी प्रश्न?")
        self.assertEqual(questions[0].option("D"), "Four")

    def test_excel_contains_no_latex_commands(self):
        value = readable_math(r"0.\overline{48}, \frac{p}{q}, q \neq 0, x^2")
        self.assertNotIn("\\", value)
        self.assertIn("p⁄q", value)
        self.assertIn("≠", value)
        self.assertIn("x²", value)

    def test_scientific_unicode_and_contextual_hindi(self):
        value = readable_math(
            r"90^\circ, \Omega, \text{CO}_2, \text{Ca(OH)}_2, "
            r"\text{Al}_4\text{C}_3, \text{Pb}(\text{NO}_3)_2 "
            r"\xrightarrow{\Delta} \text{PbO}"
        )
        self.assertEqual(value, "90°, Ω, CO₂, Ca(OH)₂, Al₄C₃, Pb(NO₃)₂ ─Δ→ PbO")
        self.assertEqual(readable_math("जूल/कूलोम (J/C)"), "जूल/कूलॉम (J/C)")
        self.assertEqual(readable_math("कूलोम"), "कूलोम")

    def test_combined_questions_and_options_are_arranged(self):
        html = (
            "<p>4. First question? (A) One (B) Two (C) Three (D) Four "
            "5. Second question? (A) A (B) B (C) C (D) D</p>"
        )
        lines = html_logical_lines(html, "ListGroup")
        self.assertEqual(
            [line.text for line in lines],
            ["4. First question?", "(A) One", "(B) Two", "(C) Three", "(D) Four",
             "5. Second question?", "(A) A", "(B) B", "(C) C", "(D) D"],
        )

    def test_horizontal_cd_pair_is_separated_but_nested_references_are_not(self):
        pair = html_logical_lines("<p>(C) 1989 (D) 1947</p>", "Text")
        self.assertEqual([line.text for line in pair], ["(C) 1989", "(D) 1947"])
        nested = html_logical_lines("<p>(D) (B) and (C) both</p>", "Text")
        self.assertEqual([line.text for line in nested], ["(D) (B) and (C) both"])
    def test_roman_chemistry_list_keeps_separate_equations(self):
        html = (
            '<ol type="i"><li><math>\\text{Fe}_2\\text{O}_3</math></li>'
            '<li><math>\\text{CO}_2</math></li></ol>'
        )
        lines = html_logical_lines(html, "ListGroup")
        self.assertEqual([line.text for line in lines], ["i) [[MATH:0]]", "ii) [[MATH:0]]"])
        self.assertEqual(lines[0].maths, [r"\text{Fe}_2\text{O}_3"])

    def test_adjacent_variable_powers_are_separate_math_atoms(self):
        self.assertEqual(MATH_ATOM.findall("x^3y^2"), ["x^3", "y^2"])
        self.assertEqual(MATH_ATOM.findall("x^2y^3"), ["x^2", "y^3"])


if __name__ == "__main__":
    unittest.main()
