"""Report generation: Markdown and HTML dashboards.

build_markdown(results, ...) renders the per-case table, per-metric
summary, failure analysis and (optionally) the v1/v2 comparison.
build_html wraps the same content in a styled single-file page.
"""

import html

from . import analyze
from .run_eval import summarize


def _pct(x):
    return "%d%%" % round(x * 100)


def build_markdown(results, title="Agent evaluation report",
                   comparison=None):
    """Render a Markdown dashboard for one result set."""
    lines = ["# " + title, ""]
    summary = summarize(results)

    lines.append("## Per-metric summary")
    lines.append("")
    lines.append("| Metric | Avg score | Pass rate | n |")
    lines.append("| --- | --- | --- | --- |")
    for name, s in summary.items():
        lines.append("| %s | %.2f | %s | %d |"
                     % (name, s["average"], _pct(s["pass_rate"]), s["n"]))
    lines.append("")

    lines.append("## Per-case results")
    lines.append("")
    lines.append("| Case | Passed all | Failed metrics |")
    lines.append("| --- | --- | --- |")
    for r in results:
        failed = [n for n, m in r.metric_results.items() if not m.passed]
        lines.append("| %s | %s | %s |"
                     % (r.case_id, "yes" if r.passed_all else "no",
                        ", ".join(failed) if failed else "-"))
    lines.append("")

    lines.append("## Failure analysis")
    lines.append("")
    patterns = analyze.detect_patterns(results)
    if not patterns:
        lines.append("No recurring failure patterns detected.")
    for p in patterns:
        lines.append("### %s" % p["id"])
        lines.append("")
        lines.append("- Description: %s" % p["description"])
        lines.append("- Evidence: %s" % ", ".join(p["evidence"]))
        lines.append("- Suggested fix: %s" % p["suggested_fix"])
        lines.append("")
    worst = analyze.top_failing_metrics(results)
    if worst:
        lines.append("Worst metrics: %s"
                     % ", ".join("%s (%d failures)" % (m, c)
                                 for m, c in worst))
        lines.append("")

    if comparison is not None:
        lines.append("## Before / after (v1 vs v2)")
        lines.append("")
        lines.append("| Metric | v1 avg | v2 avg | Delta |")
        lines.append("| --- | --- | --- | --- |")
        for name, c in comparison["comparison"].items():
            lines.append("| %s | %.2f | %.2f | %+.2f |"
                         % (name, c["v1_average"], c["v2_average"],
                            c["delta"]))
        lines.append("")
        fixed = [p["id"] for p in comparison["v1_patterns"]
                 if p["id"] not in
                 {q["id"] for q in comparison["v2_patterns"]}]
        if fixed:
            lines.append("Patterns fixed in v2: %s" % ", ".join(fixed))
            lines.append("")
    return "\n".join(lines)


def build_html(results, title="Agent evaluation report", comparison=None):
    """Render a single-file HTML dashboard."""
    summary = summarize(results)
    rows = "".join(
        "<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%d</td></tr>"
        % (html.escape(name), s["average"], _pct(s["pass_rate"]), s["n"])
        for name, s in summary.items())
    case_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (html.escape(r.case_id),
           "yes" if r.passed_all else "no",
           html.escape(", ".join(n for n, m in r.metric_results.items()
                                  if not m.passed) or "-"))
        for r in results)
    comp_section = ""
    if comparison is not None:
        comp_rows = "".join(
            "<tr><td>%s</td><td>%.2f</td><td>%.2f</td><td>%+.2f</td></tr>"
            % (html.escape(name), c["v1_average"], c["v2_average"],
               c["delta"])
            for name, c in comparison["comparison"].items())
        comp_section = (
            "<h2>Before / after (v1 vs v2)</h2>"
            "<table><tr><th>Metric</th><th>v1 avg</th><th>v2 avg</th>"
            "<th>Delta</th></tr>%s</table>" % comp_rows)
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>%(title)s</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;
padding:0 1em;color:#222}
table{border-collapse:collapse;width:100%%;margin:1em 0}
th,td{border:1px solid #ccc;padding:.4em .6em;text-align:left}
th{background:#f4f4f4}
h1{border-bottom:2px solid #222;padding-bottom:.3em}
</style></head>
<body>
<h1>%(title)s</h1>
<p>Deterministic offline evaluation. All data fictional.</p>
<h2>Per-metric summary</h2>
<table><tr><th>Metric</th><th>Avg score</th><th>Pass rate</th>
<th>n</th></tr>%(rows)s</table>
<h2>Per-case results</h2>
<table><tr><th>Case</th><th>Passed all</th><th>Failed metrics</th></tr>
%(case_rows)s</table>
%(comp_section)s
</body></html>""" % {"title": html.escape(title), "rows": rows,
                      "case_rows": case_rows, "comp_section": comp_section}
