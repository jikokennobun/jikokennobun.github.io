#!/usr/bin/env python3
"""Build a lightweight research dashboard for the incompleteness project.

The script intentionally uses only the Python standard library so it can run
from a local checkout or GitHub Actions without a dependency installation step.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urldefrag, urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research-automation.json"
DEFAULT_MARKDOWN = ROOT / "research-dashboard.md"
DEFAULT_JSON = ROOT / "research-dashboard.json"

SKIP_DIRS = {".git", "_backups"}
SCAN_SUFFIXES = {".html", ".pdf", ".svg"}
HTML_SUFFIX = ".html"
LINK_ATTRS = {"href", "src"}
THEOREM_MARKERS = [
    "Definition",
    "Theorem",
    "Proposition",
    "Lemma",
    "Corollary",
    "Construction",
    "Example",
    "Question",
    "Working question",
    "Caution",
    "Warning",
    "Proof sketch",
]


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def relpath(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def iter_project_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in SCAN_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: relpath(item))


class AuditHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[dict[str, str]] = []
        self.headings: list[dict[str, Any]] = []
        self.text_parts: list[str] = []
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs if value is not None}
        if "id" in attr_map:
            self.ids.add(attr_map["id"])
        for attr in LINK_ATTRS:
            if attr in attr_map:
                self.links.append({"attr": attr, "target": attr_map[attr]})
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._heading_tag:
            self._heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag == tag:
            heading = clean_text("".join(self._heading_parts))
            if heading:
                self.headings.append({"level": int(tag[1]), "text": heading[:180]})
            self._heading_tag = None
            self._heading_parts = []

    @property
    def text(self) -> str:
        return clean_text(" ".join(self.text_parts))


@dataclass
class HtmlAudit:
    path: Path
    ids: set[str] = field(default_factory=set)
    links: list[dict[str, str]] = field(default_factory=list)
    headings: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    raw: str = ""


def parse_html(path: Path) -> HtmlAudit:
    raw = read_text(path)
    parser = AuditHtmlParser()
    parser.feed(raw)
    return HtmlAudit(
        path=path,
        ids=parser.ids,
        links=parser.links,
        headings=parser.headings,
        text=parser.text,
        raw=raw,
    )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def local_target_path(source: Path, target: str) -> tuple[Path | None, str | None, str | None]:
    base, fragment = urldefrag(target)
    parsed = urlparse(base)

    if parsed.scheme in {"http", "https", "mailto", "tel", "data", "javascript"}:
        return None, None, None
    if parsed.scheme:
        return None, None, None
    if not parsed.path and fragment:
        return source, fragment, None
    if not parsed.path:
        return None, None, None

    decoded_path = unquote(parsed.path)
    if decoded_path.startswith("/"):
        candidate = ROOT / decoded_path.lstrip("/")
    else:
        candidate = source.parent / decoded_path

    try:
        candidate = candidate.resolve()
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return candidate, fragment or None, "target escapes repository"

    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate, fragment or None, None


def check_local_link(
    source: Path,
    link: dict[str, str],
    html_cache: dict[Path, HtmlAudit],
) -> dict[str, Any] | None:
    target, fragment, error = local_target_path(source, link["target"])
    if target is None:
        return None

    if error:
        return {
            "source": relpath(source),
            "attr": link["attr"],
            "target": link["target"],
            "problem": error,
        }

    if not target.exists():
        return {
            "source": relpath(source),
            "attr": link["attr"],
            "target": link["target"],
            "problem": "missing local target",
        }

    if fragment and target.suffix.lower() == HTML_SUFFIX:
        audit = html_cache.get(target)
        if audit is None:
            audit = parse_html(target)
            html_cache[target] = audit
        if unquote(fragment) not in audit.ids:
            return {
                "source": relpath(source),
                "attr": link["attr"],
                "target": link["target"],
                "problem": f"missing fragment #{fragment}",
            }
    return None


def marker_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for marker in THEOREM_MARKERS:
        pattern = r"\b" + re.escape(marker) + r"\b"
        count = len(re.findall(pattern, text, flags=re.IGNORECASE))
        if count:
            counts[marker] = count
    return counts


def strip_inline_html(line: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", line))


def queue_items(path: Path, raw: str, markers: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    lowered_markers = sorted(
        [(marker, marker.lower()) for marker in markers],
        key=lambda item: len(item[1]),
        reverse=True,
    )
    in_ignored_block = False
    for line_number, line in enumerate(raw.splitlines(), start=1):
        lowered_line = line.lower()
        if re.search(r"<(style|script)\b", lowered_line):
            in_ignored_block = True
        if in_ignored_block:
            if re.search(r"</(style|script)>", lowered_line):
                in_ignored_block = False
            continue

        rendered = strip_inline_html(line)
        if not rendered:
            continue
        lowered = rendered.lower()
        line_matches = [
            (marker, lowered_marker)
            for marker, lowered_marker in lowered_markers
            if lowered_marker in lowered
        ]
        for marker, lowered_marker in lowered_markers:
            if (marker, lowered_marker) not in line_matches:
                continue
            if any(
                marker != other_marker and lowered_marker in other_lowered
                for other_marker, other_lowered in line_matches
            ):
                continue
            if (line_number, marker) in seen:
                continue
            found.append(
                {
                    "path": relpath(path),
                    "line": line_number,
                    "marker": marker,
                    "text": rendered[:240],
                }
            )
            seen.add((line_number, marker))
    return found


def term_hits(text: str, path: Path, terms: list[str]) -> list[str]:
    haystack = f"{relpath(path)}\n{text}".lower()
    hits = []
    for term in terms:
        if term.lower() in haystack:
            hits.append(term)
    return sorted(set(hits), key=lambda item: item.lower())


def scan_artifact(
    path: Path,
    html_cache: dict[Path, HtmlAudit],
    terms: list[str],
    queue_markers: list[str],
) -> dict[str, Any]:
    suffix = path.suffix.lower()
    audit = html_cache.get(path) if suffix == HTML_SUFFIX else None
    raw = audit.raw if audit else ""
    text = audit.text if audit else relpath(path)

    return {
        "path": relpath(path),
        "kind": suffix.lstrip("."),
        "size_bytes": path.stat().st_size,
        "signals": term_hits(text, path, terms),
        "headings": audit.headings if audit else [],
        "marker_counts": marker_counts(text),
        "math_markers": len(re.findall(r"\\\(|\\\[|\$\$", raw)),
        "queue": queue_items(path, raw, queue_markers) if audit else [],
    }


def build_report(config: dict[str, Any]) -> dict[str, Any]:
    terms = list(config.get("global_terms", []))
    for workstream in config.get("workstreams", []):
        terms.extend(workstream.get("terms", []))
    terms = sorted(set(terms), key=lambda item: item.lower())
    queue_markers = list(config.get("queue_markers", []))

    html_paths = [path for path in iter_project_files() if path.suffix.lower() == HTML_SUFFIX]
    html_cache = {path.resolve(): parse_html(path) for path in html_paths}

    broken_links: list[dict[str, Any]] = []
    for audit in html_cache.values():
        for link in audit.links:
            problem = check_local_link(audit.path.resolve(), link, html_cache)
            if problem:
                broken_links.append(problem)

    artifacts = [
        scan_artifact(path.resolve(), html_cache, terms, queue_markers)
        for path in iter_project_files()
    ]

    queue = [
        item
        for artifact in artifacts
        for item in artifact.get("queue", [])
    ]

    workstreams = []
    for stream in config.get("workstreams", []):
        stream_terms = set(stream.get("terms", []))
        seeds = set(stream.get("seed_artifacts", []))
        matched = []
        counter: Counter[str] = Counter()
        for artifact in artifacts:
            if artifact["path"] in seeds or stream_terms.intersection(artifact["signals"]):
                matched.append(artifact["path"])
                counter.update(artifact["marker_counts"])
        workstreams.append(
            {
                "id": stream.get("id"),
                "title": stream.get("title"),
                "terms": sorted(stream_terms, key=lambda item: item.lower()),
                "artifacts": sorted(set(matched)),
                "marker_counts": dict(sorted(counter.items())),
            }
        )

    by_kind = Counter(artifact["kind"] for artifact in artifacts)
    return {
        "project": config.get("project", "research automation"),
        "focus": config.get("focus", ""),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "artifacts": len(artifacts),
            "html": by_kind.get("html", 0),
            "pdf": by_kind.get("pdf", 0),
            "svg": by_kind.get("svg", 0),
            "broken_local_links": len(broken_links),
            "queue_items": len(queue),
        },
        "workstreams": workstreams,
        "broken_links": broken_links,
        "queue": queue,
        "artifacts": artifacts,
    }


def md_escape(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_marker_summary(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))


def render_markdown(report: dict[str, Any], max_queue: int) -> str:
    lines: list[str] = []
    lines.append("# Research automation dashboard")
    lines.append("")
    lines.append(f"Generated: `{report['generated_at']}`")
    lines.append("")
    lines.append(report.get("focus", ""))
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---: |")
    for key, value in report["summary"].items():
        lines.append(f"| `{md_escape(key)}` | {md_escape(value)} |")
    lines.append("")
    lines.append("## Workstreams")
    lines.append("")
    for stream in report["workstreams"]:
        terms = ", ".join(f"`{term}`" for term in stream["terms"]) or "-"
        artifacts = ", ".join(f"`{path}`" for path in stream["artifacts"]) or "-"
        lines.append(f"### {stream['title']}")
        lines.append("")
        lines.append(f"- Terms: {terms}")
        lines.append(f"- Artifacts: {artifacts}")
        lines.append(f"- Markers: {render_marker_summary(stream['marker_counts'])}")
        lines.append("")
    lines.append("## Research Queue")
    lines.append("")
    if report["queue"]:
        lines.append("| Marker | Location | Text |")
        lines.append("| --- | --- | --- |")
        for item in report["queue"][:max_queue]:
            location = f"`{item['path']}:{item['line']}`"
            lines.append(
                f"| `{md_escape(item['marker'])}` | {location} | {md_escape(item['text'])} |"
            )
        if len(report["queue"]) > max_queue:
            lines.append("")
            lines.append(f"_Truncated to {max_queue} of {len(report['queue'])} queue items._")
    else:
        lines.append("No queue markers found.")
    lines.append("")
    lines.append("## Link Audit")
    lines.append("")
    if report["broken_links"]:
        lines.append("| Source | Target | Problem |")
        lines.append("| --- | --- | --- |")
        for item in report["broken_links"]:
            lines.append(
                f"| `{md_escape(item['source'])}` | `{md_escape(item['target'])}` | {md_escape(item['problem'])} |"
            )
    else:
        lines.append("No broken local links found.")
    lines.append("")
    lines.append("## Artifact Inventory")
    lines.append("")
    lines.append("| Kind | Path | Signals | Markers |")
    lines.append("| --- | --- | --- | --- |")
    for artifact in report["artifacts"]:
        signals = ", ".join(f"`{signal}`" for signal in artifact["signals"]) or "-"
        markers = render_marker_summary(artifact["marker_counts"])
        lines.append(
            f"| `{artifact['kind']}` | `{md_escape(artifact['path'])}` | {signals} | {md_escape(markers)} |"
        )
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    lines.append("- Refresh dashboard on Windows: `./tools/research_automation.cmd --write`")
    lines.append("- Check local links on Windows: `./tools/research_automation.cmd --check`")
    lines.append("- Cross-platform Python entry point: `python tools/research_automation.py --write`")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], markdown_path: Path, json_path: Path, max_queue: int) -> None:
    markdown_path.write_text(render_markdown(report, max_queue), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--max-queue", type=int, default=50)
    parser.add_argument("--write", action="store_true", help="write Markdown and JSON dashboards")
    parser.add_argument("--check", action="store_true", help="fail if broken local links are found")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    report = build_report(config)

    if args.write:
        write_report(report, args.markdown, args.json, args.max_queue)
        print(f"Wrote {relpath(args.markdown)} and {relpath(args.json)}")

    summary = report["summary"]
    print(
        "Research audit: "
        f"{summary['artifacts']} artifacts, "
        f"{summary['broken_local_links']} broken local links, "
        f"{summary['queue_items']} queue items."
    )

    if args.check and report["broken_links"]:
        for item in report["broken_links"]:
            print(
                f"{item['source']}: {item['target']} -> {item['problem']}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
