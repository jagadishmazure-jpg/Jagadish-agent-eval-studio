"""Tests for the Markdown/HTML report generation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation import improve
from evaluation.report import build_html, build_markdown
from evaluation.run_eval import run_all


def test_markdown_has_all_sections():
    md = build_markdown(run_all(version="v2"))
    for section in ("## Per-metric summary", "## Per-case results",
                    "## Failure analysis"):
        assert section in md


def test_markdown_lists_all_cases():
    md = build_markdown(run_all(version="v2"))
    for case_id in ("valid-full-refund", "fraud-never-purchased",
                    "invalid-user"):
        assert case_id in md


def test_markdown_marks_failures():
    md = build_markdown(run_all(version="v1"))
    assert "invalid-user" in md
    assert "answer_correctness" in md  # listed as a failed metric


def test_markdown_comparison_section():
    cmp = improve.compare_versions()
    md = build_markdown(cmp["v2_results"], comparison=cmp)
    assert "## Before / after (v1 vs v2)" in md
    assert "step_efficiency" in md


def test_html_is_single_file():
    html = build_html(run_all(version="v2"))
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert "task_completion" in html


def test_html_escapes_content():
    html = build_html(run_all(version="v2"), title="<Test & Report>")
    assert "<Test & Report>" not in html
    assert "&lt;Test &amp; Report&gt;" in html


def test_html_comparison_section():
    cmp = improve.compare_versions()
    html = build_html(cmp["v2_results"], comparison=cmp)
    assert "Before / after" in html
