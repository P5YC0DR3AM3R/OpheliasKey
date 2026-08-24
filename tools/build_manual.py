#!/usr/bin/env python3
"""Build the manual/ pages from README.md and docs/ARCHITECTURE.md.

The site is static on purpose: the same files work on GitHub Pages, on
`python3 -m http.server`, and from file://. After editing the README or
the architecture notes, regenerate with:

    pip install markdown
    python3 tools/build_manual.py
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "manual"

PAGES = [
    # (output file, sidebar label, page title, source)
    ("index.html", "The manual", "The Manual", ROOT / "README.md"),
    ("architecture.html", "Architecture", "Architecture", ROOT / "docs" / "ARCHITECTURE.md"),
]

LEDES = {
    "index.html": "Everything <code>okey</code> ingests, derives and reports — and why it "
                  "would rather say <code>NULL</code> than guess.",
    "architecture.html": "Design notes: the pipeline, the immutable raw store, refusal as a "
                         "principle, and how the studio model stays honest.",
}


def prepare_readme(text: str) -> str:
    """Trim repo-page chrome that the site shell replaces."""
    # Everything before the first horizontal rule is the GitHub banner block.
    cut = text.find("\n---\n")
    if cut != -1:
        text = text[cut + len("\n---\n"):]
    # The sidebar is the table of contents here.
    text = re.sub(r"## Table of Contents\n.*?(?=\n## )", "", text, flags=re.S)
    # The shell has its own footer.
    text = re.sub(r"<!-- Animated Wave Footer -->.*\Z", "", text, flags=re.S)
    return text


def prepare_architecture(text: str) -> str:
    # The shell renders the page title; drop the markdown h1.
    return re.sub(r"\A# Architecture\s*\n", "", text)


PREPARE = {"index.html": prepare_readme, "architecture.html": prepare_architecture}


SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — Ophelia's Key</title>
<meta name="description" content="Documentation for Ophelia's Key (okey), purchase intelligence for a liveaboard refit.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%23050a12'/%3E%3Cpath d='M4 21h20l4-6H6z' fill='none' stroke='%234fe3ff' stroke-width='1.6'/%3E%3Ccircle cx='16' cy='10' r='2.4' fill='%23ffb347'/%3E%3Cpath d='M2 25h28' stroke='%234fe3ff' stroke-opacity='.5'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    color-scheme: dark;
    --ground: #050a12; --surface: #0b1420; --surface-2: #0f1b2b;
    --line: #17263a; --line-2: #1f3249;
    --ink: #dbe8f5; --ink-2: #a9bbcf; --dim: #7f95ad; --mute: #4d6178;
    --holo: #4fe3ff; --holo-soft: rgba(79,227,255,.14); --holo-line: rgba(79,227,255,.55);
    --warm: #ffb347;
    --display: "Chakra Petch", "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
    --sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html { background: var(--ground); }
  body {
    margin: 0; background: var(--ground); color: var(--ink);
    font: 15px/1.62 var(--sans); -webkit-font-smoothing: antialiased;
    background-image:
      radial-gradient(1100px 500px at 50% -10%, rgba(79,227,255,.06), transparent 60%),
      linear-gradient(rgba(79,227,255,.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(79,227,255,.03) 1px, transparent 1px);
    background-size: auto, 48px 48px, 48px 48px;
  }
  body::after {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 50;
    background: repeating-linear-gradient(0deg, rgba(255,255,255,.02) 0 1px, transparent 1px 3px);
    mix-blend-mode: screen;
  }
  a { color: var(--holo); text-decoration: none; } a:hover { text-decoration: underline; }

  /* ---------- top bar ---------- */
  .mast { position: sticky; top: 0; z-index: 40; background: rgba(5,10,18,.92);
          backdrop-filter: blur(6px); border-bottom: 1px solid var(--line); }
  .mast-in { max-width: 1180px; margin: 0 auto; padding: 13px 22px;
             display: flex; justify-content: space-between; align-items: center; gap: 12px 24px; flex-wrap: wrap; }
  .eyebrow { font: 500 11px/1 var(--mono); letter-spacing: .16em; text-transform: uppercase;
             color: var(--dim); display: flex; gap: 14px; flex-wrap: wrap; }
  .eyebrow b { color: var(--holo); font-weight: 500; }
  .eyebrow a { color: inherit; } .eyebrow a:hover { color: var(--holo); text-decoration: none; }
  .topnav { display: flex; gap: 8px; flex-wrap: wrap; }
  .chip { display: inline-flex; align-items: center; gap: 6px; font: 500 11px/1 var(--mono);
          letter-spacing: .08em; text-transform: uppercase; padding: 6px 9px;
          border: 1px solid var(--line-2); color: var(--ink-2); background: var(--surface); }
  a.chip:hover { text-decoration: none; border-color: var(--holo-line); color: var(--holo); }
  .chip.here { border-color: var(--holo-line); color: var(--holo); }

  /* ---------- frame ---------- */
  .frame { max-width: 1180px; margin: 0 auto; padding: 28px 22px 80px;
           display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 34px; align-items: start; }
  @media (max-width: 900px) { .frame { grid-template-columns: minmax(0, 1fr); } }

  /* ---------- sidebar ---------- */
  .side { position: sticky; top: 66px; max-height: calc(100vh - 82px); overflow-y: auto;
          padding-right: 6px; scrollbar-width: thin; }
  .side h4 { margin: 18px 0 8px; font: 600 10px/1 var(--mono); letter-spacing: .18em;
             text-transform: uppercase; color: var(--mute); }
  .side h4:first-child { margin-top: 2px; }
  .side h4::before { content: "// "; }
  .side a { display: block; padding: 5px 10px; margin: 1px 0; font: 500 12px/1.4 var(--mono);
            letter-spacing: .04em; color: var(--dim); border-left: 1px solid var(--line-2); }
  .side a:hover { color: var(--holo); text-decoration: none; border-left-color: var(--holo-line); }
  .side a.page { color: var(--ink-2); text-transform: uppercase; letter-spacing: .1em; font-size: 11px; }
  .side a.page.here { color: var(--holo); border-left-color: var(--holo); background: var(--holo-soft); }
  .side a.on { color: var(--holo); border-left-color: var(--holo); }
  @media (max-width: 900px) {
    .side { position: static; max-height: none; border: 1px solid var(--line);
            background: var(--surface); padding: 6px 14px 14px; }
    details.menu summary { cursor: pointer; list-style: none; font: 600 11px/1 var(--mono);
      letter-spacing: .16em; text-transform: uppercase; color: var(--holo); padding: 10px 0; }
    details.menu summary::before { content: "▤ "; }
  }
  @media (min-width: 901px) { details.menu { display: contents; } details.menu summary { display: none; } }

  /* ---------- document ---------- */
  .doc { min-width: 0; }
  .doc-head { border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 26px; }
  .doc-head h1 { margin: 0 0 8px; font: 700 clamp(26px, 4vw, 38px)/1.05 var(--display);
                 letter-spacing: .01em; text-transform: uppercase; }
  .doc-head h1 span { color: var(--holo); text-shadow: 0 0 18px rgba(79,227,255,.4); }
  .doc-head p { margin: 0; color: var(--ink-2); max-width: 72ch; }
  .doc-head p code { font-size: 13px; }

  .doc h2 { margin: 44px 0 14px; font: 700 20px/1.2 var(--display); letter-spacing: .03em;
            text-transform: uppercase; color: var(--ink); padding-bottom: 8px;
            border-bottom: 1px solid var(--line); }
  .doc h2::before { content: "// "; color: var(--holo); }
  .doc h3 { margin: 26px 0 8px; font: 600 15px/1.3 var(--display); letter-spacing: .04em;
            text-transform: uppercase; color: var(--ink); }
  .doc h2[id], .doc h3[id] { scroll-margin-top: 74px; }
  .doc p, .doc li { color: var(--ink-2); }
  .doc p { margin: 0 0 12px; max-width: 78ch; }
  .doc strong { color: var(--ink); }
  .doc em { color: var(--ink); font-style: italic; }
  .doc ul, .doc ol { margin: 0 0 14px; padding-left: 22px; }
  .doc li { margin-bottom: 5px; }
  .doc hr { border: 0; border-top: 1px solid var(--line); margin: 34px 0; }
  .doc code { font: 12.5px var(--mono); background: var(--surface-2); border: 1px solid var(--line);
              padding: 1px 6px; border-radius: 3px; color: var(--ink-2); }
  .doc pre { background: #030710; border: 1px solid var(--line); padding: 13px 16px; margin: 0 0 14px;
             font: 12.5px/1.6 var(--mono); color: var(--ink-2); overflow-x: auto; }
  .doc pre code { background: none; border: 0; padding: 0; font-size: inherit; color: inherit; }
  .doc pre[align="center"] { text-align: left; background: transparent; border: 0;
      color: var(--holo); text-shadow: 0 0 8px rgba(79,227,255,.45);
      font-size: clamp(4.5px, calc((100vw - 120px) / 102), 10px); line-height: 1.18; }
  .doc blockquote { margin: 0 0 14px; padding: 12px 16px; border-left: 2px solid var(--holo-line);
                    background: var(--surface); }
  .doc blockquote p { margin-bottom: 8px; } .doc blockquote p:last-child { margin-bottom: 0; }
  .doc table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin: 0 0 18px;
               display: block; overflow-x: auto; }
  .doc th { font: 600 10.5px/1.3 var(--mono); letter-spacing: .1em; text-transform: uppercase;
            color: var(--dim); text-align: left; padding: 0 14px 8px 0;
            border-bottom: 1px solid var(--line-2); white-space: nowrap; }
  .doc td { padding: 8px 14px 8px 0; border-bottom: 1px solid var(--line); color: var(--ink-2);
            vertical-align: top; }
  .doc tr:last-child td { border-bottom: 0; }
  .doc img { max-width: 100%; }

  /* ---------- footer ---------- */
  footer { border-top: 1px solid var(--line); }
  .foot { max-width: 1180px; margin: 0 auto; padding: 20px 22px 34px;
          display: flex; justify-content: space-between; gap: 12px 30px; flex-wrap: wrap;
          font: 400 12.5px/1.6 var(--sans); color: var(--mute); }
</style>
</head>
<body>

<header class="mast">
  <div class="mast-in">
    <div class="eyebrow">
      <span><a href="../"><b>OPHELIA'S KEY</b></a></span>
      <span>SHIP'S&nbsp;MANUAL</span>
    </div>
    <nav class="topnav">
      <a class="chip" href="../">Home</a>
__TOPNAV__
      <a class="chip" href="https://github.com/P5YC0DR3AM3R/OpheliasKey">GitHub</a>
    </nav>
  </div>
</header>

<div class="frame">

  <nav class="side" aria-label="Manual navigation">
    <details class="menu" open>
      <summary>Menu</summary>
      <h4>Pages</h4>
__PAGES__
      <h4>On this page</h4>
__SECTIONS__
    </details>
  </nav>

  <main class="doc">
    <div class="doc-head">
      <h1><span>__TITLE__</span></h1>
      <p>__LEDE__</p>
    </div>
__CONTENT__
  </main>

</div>

<footer>
  <div class="foot">
    <span>© 2025–2026 Phygital DevOps Inc. · Micah Read</span>
    <span>Generated from the repository markdown by <code style="font-family:var(--mono);font-size:11px">tools/build_manual.py</code></span>
  </div>
</footer>

<script>
  // Scroll-spy: light up the section the reader is in.
  (function () {
    var links = {};
    document.querySelectorAll('.side a[href^="#"]').forEach(function (a) {
      links[a.getAttribute('href').slice(1)] = a;
    });
    var current = null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && links[e.target.id]) {
          if (current) current.classList.remove('on');
          current = links[e.target.id];
          current.classList.add('on');
        }
      });
    }, { rootMargin: '-60px 0px -70% 0px' });
    document.querySelectorAll('.doc h2[id]').forEach(function (h) { io.observe(h); });
  })();
</script>

</body>
</html>
"""


def build_page(out_name: str, title: str, source: Path) -> None:
    text = PREPARE[out_name](source.read_text(encoding="utf-8"))
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"slugify": slugify, "toc_depth": "2-3"}},
    )
    content = md.convert(text)

    sections = "\n".join(
        f'      <a href="#{tok["id"]}">{tok["name"]}</a>'
        for tok in md.toc_tokens
        if tok["level"] == 2
    )
    pages = "\n".join(
        f'      <a class="page{" here" if fn == out_name else ""}" href="{fn}">{label}</a>'
        for fn, label, _, _ in PAGES
    )
    topnav = "\n".join(
        f'      <a class="chip{" here" if fn == out_name else ""}" href="{fn}">{label}</a>'
        for fn, label, _, _ in PAGES
    )

    html = (
        SHELL.replace("__TITLE__", title)
        .replace("__LEDE__", LEDES[out_name])
        .replace("__TOPNAV__", topnav)
        .replace("__PAGES__", pages)
        .replace("__SECTIONS__", sections)
        .replace("__CONTENT__", content)
    )
    OUT.mkdir(exist_ok=True)
    (OUT / out_name).write_text(html, encoding="utf-8")
    print(f"built manual/{out_name}  ({len(md.toc_tokens)} sections from {source.name})")


def main() -> None:
    for out_name, _, title, source in PAGES:
        build_page(out_name, title, source)


if __name__ == "__main__":
    main()
