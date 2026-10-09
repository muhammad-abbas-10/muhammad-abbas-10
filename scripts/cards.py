#!/usr/bin/env python3
"""
cards.py - render GitHub stat, activity and project cards as SVGs. Stdlib only.

Replaces github-readme-stats / github-profile-trophy / streak-stats, which are
shared public instances that go down (503), run out of quota (402) or time out.
These are files in your own repo, so they render as long as GitHub renders.

    python scripts/cards.py --user muhammad-abbas-10 --out assets

Writes into <out>:
  card-stats-{dark,light}.svg      at-a-glance numbers
  card-activity-{dark,light}.svg   contribution heatmap (last year)
  card-<id>-{dark,light}.svg       one card per entry in assets/projects.json

Contribution numbers come from the GraphQL API when $GITHUB_TOKEN can read them,
and otherwise from the public contribution calendar on github.com, so no
personal access token is required.

Projects: an entry with a "repo" that exists on the account gets live language /
star / fork data. An entry without one (or whose repo is private or not found) is
still drawn from projects.json alone, so a card never silently disappears.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = {"User-Agent": "cards.py"}

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "title": "#aa9bef",
        "text": "#c9d1d9", "muted": "#8b949e", "value": "#e6edf3",
        "accent": "#aa9bef",
        "heat": ["#161b22", "#2e2a5a", "#4b3f9a", "#7d6ee0", "#aa9bef"],
    },
    "light": {
        "bg": "#ffffff", "border": "#d0d7de", "title": "#1a7f37",
        "text": "#1f2328", "muted": "#57606a", "value": "#1f2328",
        "accent": "#1a7f37",
        "heat": ["#ebedf0", "#c9f0d3", "#6fd58a", "#2da44e", "#116329"],
    },
}

# GitHub linguist colours for the languages likely to show up here
LANG_COLOR = {
    "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "Python": "#3572A5",
    "HTML": "#e34c26", "CSS": "#563d7c", "C++": "#f34b7d", "C": "#555555",
    "Java": "#b07219", "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051",
    "PLpgSQL": "#336790", "Vue": "#41b883", "Ruby": "#701516", "PHP": "#4F5D95",
    "Jupyter Notebook": "#DA5B0B", "SCSS": "#c6538c", "Svelte": "#ff3e00",
}

FONT = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"

# Octicon outlines, drawn on a 16x16 grid. These are paths rather than the
# characters U+2605 / U+2442 because those codepoints are missing from a lot of
# system fonts and fall back to a tofu box.
ICON_STAR = ("M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 "
             "2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 "
             "01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25z")
ICON_FORK = ("M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75v-.878a2.25 2.25 0 "
             "111.5 0v.878a2.25 2.25 0 01-2.25 2.25h-1.5v2.128a2.251 2.251 0 11-1.5 "
             "0V8.5h-1.5A2.25 2.25 0 013.5 6.25v-.878a2.25 2.25 0 111.5 0zM5 3.25a.75.75 0 "
             "10-1.5 0 .75.75 0 001.5 0zm6.75.75a.75.75 0 100-1.5.75.75 0 000 1.5zm-3 "
             "8.75a.75.75 0 100-1.5.75.75 0 000 1.5z")
ICON_REPO = ("M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 "
             "0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 "
             "012 11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 "
             "12.25v3.25a.25.25 0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 "
             "00.4-.2v-3.25a.25.25 0 00-.25-.25h-3.5a.25.25 0 00-.25.25z")


# Hand-drawn 16x16 project icons (fill-rule evenodd, so inner shapes are cut-outs).
ICON_PIZZA = ("M8 15.5 1.3 3.4C3 2.5 5.5 1.8 8 1.8s5 .7 6.7 1.6L8 15.5z"
              "M6 4.6a1.1 1.1 0 100 2.2 1.1 1.1 0 000-2.2z"
              "M9.9 6.6a1 1 0 100 2 1 1 0 000-2z"
              "M7.9 9.6a.9.9 0 100 1.8.9.9 0 000-1.8z")
ICON_COFFEE = ("M2 5.5h9.5v5A3.5 3.5 0 018 14H5.5A3.5 3.5 0 012 10.5v-5z"
               "M11.5 6.6h.9a2 2 0 010 4h-.9V9.4h.9a.8.8 0 000-1.6h-.9V6.6z"
               "M1 14.7h12v1H1z"
               "M4.2 1.2h1.1v2.6H4.2zM7.2 1.2h1.1v2.6H7.2z")
ICON_RECEIPT = ("M3 1h10v14l-1.67-1.2L9.67 15 8 13.8 6.33 15l-1.66-1.2L3 15V1z"
                "M5.5 4.2h5v1.2h-5zM5.5 7h5v1.2h-5zM5.5 9.8h3v1.2h-3z")
PROJECT_ICONS = {"repo": ICON_REPO, "pizza": ICON_PIZZA, "coffee": ICON_COFFEE,
                 "receipt": ICON_RECEIPT}


def icon(path, x, y, size, fill, evenodd=False):
    s = size / 16
    rule = ' fill-rule="evenodd"' if evenodd else ""
    return (f'<path transform="translate({x:.1f},{y:.1f}) scale({s:.3f})" '
            f'fill="{fill}"{rule} d="{path}"/>')


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #


def _open(req: urllib.request.Request, tries: int = 4) -> bytes:
    """GET with a few retries on rate limits / transient errors."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (403, 429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except urllib.error.URLError as e:
            last = e
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise last  # pragma: no cover


def rest(path: str, token: str | None):
    req = urllib.request.Request("https://api.github.com" + path, headers=dict(UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return json.loads(_open(req).decode())


def graphql(query: str, variables: dict, token: str):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body,
                                 headers={**UA, "Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


CONTRIB_QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}
"""


def calendar_from_graphql(user: str, token: str):
    """[(date, count)] from the GraphQL API, or None if it is not readable."""
    try:
        data = graphql(CONTRIB_QUERY, {"login": user}, token)
    except urllib.error.HTTPError as e:
        print(f"  graphql contributions unavailable (HTTP {e.code})", file=sys.stderr)
        return None
    if data.get("errors") or not data.get("data", {}).get("user"):
        msg = (data.get("errors") or [{}])[0].get("message")
        print(f"  graphql contributions unavailable: {msg}", file=sys.stderr)
        return None
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return [(dt.date.fromisoformat(d["date"]), d["contributionCount"])
            for w in cal["weeks"] for d in w["contributionDays"]]


_TAG_RE = re.compile(r"<(td|tool-tip)\b([^>]*)>(.*?)</\1>", re.S)


def calendar_from_html(user: str):
    """[(date, count)] scraped from the public contribution calendar (no token)."""
    req = urllib.request.Request(
        f"https://github.com/users/{user}/contributions",
        headers={**UA, "Accept": "text/html"})
    try:
        page = _open(req).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        print(f"  public contribution calendar unavailable: {e}", file=sys.stderr)
        return None

    cells, counts = {}, {}
    for kind, attrs, inner in _TAG_RE.findall(page):
        if kind == "td" and "ContributionCalendar-day" in attrs:
            date = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', attrs)
            cid = re.search(r'\bid="([^"]+)"', attrs)
            level = re.search(r'data-level="(\d)"', attrs)
            if date and cid:
                cells[cid.group(1)] = (dt.date.fromisoformat(date.group(1)),
                                       int(level.group(1)) if level else 0)
        elif kind == "tool-tip":
            target = re.search(r'\bfor="([^"]+)"', attrs)
            m = re.match(r"\s*(\d+)\s+contribution", inner)
            if target:
                counts[target.group(1)] = int(m.group(1)) if m else 0
    if not cells:
        print("  public contribution calendar: no day cells found", file=sys.stderr)
        return None
    # A tooltip is the exact count. Without one, fall back to the shade level.
    days = [(d, counts.get(cid, 1 if lvl else 0)) for cid, (d, lvl) in cells.items()]
    return sorted(days)


def fetch_calendar(user: str, token: str | None):
    """Best available contribution calendar: GraphQL first, public page second."""
    days = calendar_from_graphql(user, token) if token else None
    return days or calendar_from_html(user)


def calendar_stats(days):
    """(total, active_days, current_streak, longest_streak, best_day)."""
    days = sorted(days)
    total = sum(c for _, c in days)
    active = sum(1 for _, c in days if c > 0)
    best = max((c for _, c in days), default=0)

    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)

    # Today counts only if it already has activity; an empty today does not
    # break a streak that was alive yesterday.
    current = 0
    for date, c in reversed(days):
        if c > 0:
            current += 1
        elif date != days[-1][0]:
            break
    return total, active, current, longest, best


# --------------------------------------------------------------------------- #
# svg helpers
# --------------------------------------------------------------------------- #


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def text_width(s: str, size: float) -> float:
    return len(s) * size * 0.53


def wrap(text: str, size: float, max_w: float, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words:
        # did everything fit?
        used = len(" ".join(lines).split())
        if used < len(words):
            while lines and text_width(lines[-1] + "…", size) > max_w:
                lines[-1] = lines[-1].rsplit(" ", 1)[0]
            lines[-1] += "…"
    return lines


def frame(w, h, c, body, label):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{esc(label)}" '
        f'font-family="{FONT}">'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="10" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
        f"{body}</svg>"
    )


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #


def render_stats(user, stats, theme):
    c = THEMES[theme]
    pad = 22
    tiles = [(v, k) for k, v in stats]
    cols = 4 if len(tiles) >= 7 else 3
    rows = (len(tiles) + cols - 1) // cols
    # Height follows the tile count, so the card does not leave a dead band when
    # some tiles are unavailable. Measured off the last row's label baseline
    # rather than a nominal row height.
    rh, W = 46, 480
    H = pad + 52 + (rows - 1) * rh + 17 + pad
    tw = (W - 2 * pad) / cols

    out = [
        f'<text x="{pad}" y="{pad + 14}" font-size="15" font-weight="700" '
        f'fill="{c["title"]}">{esc(user)}</text>',
        f'<text x="{W - pad}" y="{pad + 14}" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">at a glance</text>',
        f'<line x1="{pad}" y1="{pad + 26}" x2="{W - pad}" y2="{pad + 26}" '
        f'stroke="{c["border"]}"/>',
    ]
    top = pad + 52
    for i, (value, label) in enumerate(tiles):
        cx = pad + (i % cols) * tw
        cy = top + (i // cols) * rh
        out.append(
            f'<text x="{cx:.0f}" y="{cy:.0f}" font-size="23" font-weight="700" '
            f'fill="{c["value"]}">{esc(value)}</text>'
        )
        out.append(
            f'<text x="{cx:.0f}" y="{cy + 17:.0f}" font-size="10.5" '
            f'fill="{c["muted"]}">{esc(label)}</text>'
        )
    return frame(W, H, c, "".join(out), f"{user} GitHub statistics")


def render_activity(user, days, theme):
    """52-week contribution heatmap with a slow scanning highlight.

    Every cell is fully visible in the base state; only the sweep animates, so a
    renderer that ignores SMIL (or freezes on the first frame) still shows data.
    """
    c = THEMES[theme]
    W, pad, label_w = 856, 22, 30
    days = sorted(days)
    first, last = days[0][0], days[-1][0]
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # Sunday-aligned
    weeks = (last - start).days // 7 + 1
    x0 = pad + label_w
    pitch = (W - pad - x0) / weeks
    cell = pitch - 3
    y0 = pad + 26 + 20
    grid_w = pitch * weeks
    grid_h = pitch * 7

    top = max((n for _, n in days), default=0)

    def level(n):
        if n <= 0 or top <= 0:
            return 0
        return min(4, max(1, -(-4 * n // top)))  # ceil(4n/top), clamped to 1..4

    total = sum(n for _, n in days)
    out = [
        f'<text x="{pad}" y="{pad + 14}" font-size="15" font-weight="700" '
        f'fill="{c["title"]}">contribution activity</text>',
        f'<text x="{W - pad}" y="{pad + 14}" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">{total:,} contributions in the last year</text>',
        f'<line x1="{pad}" y1="{pad + 26}" x2="{W - pad}" y2="{pad + 26}" '
        f'stroke="{c["border"]}"/>',
    ]

    # month labels: one per month change, skipping any that would collide
    last_label_col, prev_month = -99, None
    for w in range(weeks):
        sunday = start + dt.timedelta(days=7 * w)
        if sunday.month != prev_month:
            if w - last_label_col >= 3 and w <= weeks - 2:
                out.append(
                    f'<text x="{x0 + w * pitch:.1f}" y="{y0 - 7}" font-size="9.5" '
                    f'fill="{c["muted"]}">{sunday.strftime("%b")}</text>'
                )
                last_label_col = w
            prev_month = sunday.month

    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(
            f'<text x="{x0 - 7}" y="{y0 + row * pitch + cell - 1:.1f}" font-size="9" '
            f'text-anchor="end" fill="{c["muted"]}">{name}</text>'
        )

    cells = []
    for date, n in days:
        col = (date - start).days // 7
        row = (date.weekday() + 1) % 7
        cells.append(
            f'<rect x="{x0 + col * pitch:.2f}" y="{y0 + row * pitch:.2f}" '
            f'width="{cell:.2f}" height="{cell:.2f}" rx="2.5" '
            f'fill="{c["heat"][level(n)]}"/>'
        )
    out.append(f'<clipPath id="g"><rect x="{x0}" y="{y0 - 2}" width="{grid_w:.1f}" '
               f'height="{grid_h:.1f}"/></clipPath>')
    out.append('<g clip-path="url(#g)">' + "".join(cells))

    # scanning highlight: holds off-grid between passes
    sweep_w = 90
    out.append(
        f'<defs><linearGradient id="s" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{c["accent"]}" stop-opacity="0"/>'
        f'<stop offset="0.6" stop-color="{c["accent"]}" stop-opacity="0.22"/>'
        f'<stop offset="1" stop-color="{c["accent"]}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<rect y="{y0 - 2}" width="{sweep_w}" height="{grid_h:.1f}" fill="url(#s)" '
        f'x="{x0 - sweep_w}">'
        f'<animate attributeName="x" dur="9s" repeatCount="indefinite" '
        f'keyTimes="0;0.55;1" values="{x0 - sweep_w};{x0 + grid_w:.1f};{x0 + grid_w:.1f}"/>'
        f'</rect></g>'
    )

    # legend
    ly = y0 + grid_h + 12
    lx = W - pad - (5 * (cell + 3)) - 60
    out.append(f'<text x="{lx:.1f}" y="{ly + cell - 2:.1f}" font-size="9.5" '
               f'text-anchor="end" fill="{c["muted"]}">Less</text>')
    for i, col in enumerate(c["heat"]):
        out.append(f'<rect x="{lx + 8 + i * (cell + 3):.1f}" y="{ly:.1f}" '
                   f'width="{cell:.1f}" height="{cell:.1f}" rx="2.5" fill="{col}"/>')
    out.append(f'<text x="{lx + 8 + 5 * (cell + 3) + 4:.1f}" y="{ly + cell - 2:.1f}" '
               f'font-size="9.5" fill="{c["muted"]}">More</text>')

    H = round(ly + cell + pad)
    return frame(W, H, c, "".join(out), f"{user} contribution activity")


def render_project(card, theme):
    """A project card. Width follows card["size"]: "wide" or "half" (default)."""
    c = THEMES[theme]
    wide = card.get("size") == "wide"
    W = 856 if wide else 420
    pad = 20
    out = []

    glyph = PROJECT_ICONS.get(card.get("icon") or "repo", ICON_REPO)
    out.append(icon(glyph, pad, pad + 1, 16, c["accent"], evenodd=True))
    out.append(
        f'<text x="{pad + 24}" y="{pad + 14}" font-size="16" font-weight="700" '
        f'fill="{c["title"]}">{esc(card["title"])}</text>'
    )
    if card.get("badge"):
        bw = text_width(card["badge"], 10.5) + 20
        out.append(
            f'<rect x="{W - pad - bw:.1f}" y="{pad}" width="{bw:.1f}" height="20" rx="10" '
            f'fill="{c["accent"]}" fill-opacity="0.16" stroke="{c["accent"]}" '
            f'stroke-opacity="0.6"/>'
            f'<text x="{W - pad - bw / 2:.1f}" y="{pad + 14}" font-size="10.5" '
            f'font-weight="600" text-anchor="middle" fill="{c["accent"]}">'
            f'{esc(card["badge"])}</text>'
        )

    y = pad + 40
    lines = wrap(card.get("description") or "No description yet.", 12.5,
                 W - 2 * pad, 4 if not wide else 3)
    for line in lines:
        out.append(f'<text x="{pad}" y="{y}" font-size="12.5" fill="{c["text"]}">'
                   f'{esc(line)}</text>')
        y += 18

    # technology pills, wrapped onto as many rows as needed
    y += 8
    x = pad
    for tag in card.get("tags", []):
        tw = text_width(tag, 10.5) + 18
        if x + tw > W - pad:
            x, y = pad, y + 27
        out.append(
            f'<rect x="{x:.1f}" y="{y - 14}" width="{tw:.1f}" height="21" rx="10.5" '
            f'fill="{c["accent"]}" fill-opacity="0.12" stroke="{c["border"]}"/>'
            f'<text x="{x + tw / 2:.1f}" y="{y}" font-size="10.5" text-anchor="middle" '
            f'fill="{c["text"]}">{esc(tag)}</text>'
        )
        x += tw + 8
    if card.get("tags"):
        y += 12

    # live repository facts (only when they exist; zero counts are omitted)
    foot = []
    if card.get("language"):
        foot.append(("lang", card["language"]))
    if card.get("stars"):
        foot.append((ICON_STAR, card["stars"]))
    if card.get("forks"):
        foot.append((ICON_FORK, card["forks"]))
    if foot:
        y += 14
        x = pad
        for kind, val in foot:
            if kind == "lang":
                col = LANG_COLOR.get(val, c["muted"])
                out.append(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{col}"/>')
                out.append(f'<text x="{x + 15}" y="{y}" font-size="11" '
                           f'fill="{c["muted"]}">{esc(val)}</text>')
                x += 15 + text_width(val, 11) + 18
            else:
                out.append(icon(kind, x, y - 11, 12, c["muted"]))
                out.append(f'<text x="{x + 17}" y="{y}" font-size="11" '
                           f'fill="{c["muted"]}">{val}</text>')
                x += 17 + text_width(str(val), 11) + 18
        y += 6

    H = max(int(y + pad), int(card.get("min_height", 0)))
    return frame(W, H, c, "".join(out), f'{card["title"]} project card')


# --------------------------------------------------------------------------- #


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", required=True)
    p.add_argument("--out", type=Path, default=Path("assets"))
    p.add_argument("--projects", type=Path, default=Path("assets/projects.json"),
                   help="projects to render cards for (see the file for the schema)")
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    args.out.mkdir(parents=True, exist_ok=True)

    user = rest(f"/users/{args.user}", token)
    repos = []
    page = 1
    while True:
        batch = rest(f"/users/{args.user}/repos?per_page=100&page={page}&type=owner", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    owned = [r for r in repos if not r["fork"]]
    stars = sum(r["stargazers_count"] for r in owned)

    days = fetch_calendar(args.user, token)
    tiles = []
    if days:
        total, active, current, longest, best = calendar_stats(days)
        tiles += [("Contributions (1y)", f"{total:,}"),
                  ("Active days (1y)", f"{active:,}"),
                  ("Current streak", f"{current:,}"),
                  ("Longest streak", f"{longest:,}"),
                  ("Most in a day", f"{best:,}")]
    else:
        print("  note: no contribution data, skipping contribution tiles", file=sys.stderr)
    tiles.append(("Public repos", f"{user['public_repos']:,}"))
    # Social tiles only appear once they have something to show.
    if stars:
        tiles.append(("Total stars", f"{stars:,}"))
    if user["followers"]:
        tiles.append(("Followers", f"{user['followers']:,}"))

    for theme in ("dark", "light"):
        dest = args.out / f"card-stats-{theme}.svg"
        dest.write_text(render_stats(args.user, tiles, theme), encoding="utf-8")
    print(f"wrote card-stats-*.svg  ({len(tiles)} tiles)")

    if days:
        for theme in ("dark", "light"):
            dest = args.out / f"card-activity-{theme}.svg"
            dest.write_text(render_activity(args.user, days, theme), encoding="utf-8")
        print(f"wrote card-activity-*.svg  ({len(days)} days)")

    if not args.projects.exists():
        print(f"no {args.projects}, skipping project cards")
        return
    wanted = json.loads(args.projects.read_text(encoding="utf-8"))["projects"]
    by_name = {r["name"].lower(): r for r in repos}

    for entry in wanted:
        src = by_name.get((entry.get("repo") or "").lower())
        if entry.get("repo") and not src:
            print(f"  note: {entry['repo']} not visible on the account "
                  f"(private or renamed), drawing it from projects.json only")
        card = {
            "title": entry["title"],
            "description": entry.get("description") or (src or {}).get("description"),
            "tags": entry.get("tags", []),
            "size": entry.get("size", "half"),
            "icon": entry.get("icon"),
            "badge": entry.get("badge"),
            "min_height": entry.get("min_height", 0),
            "language": entry.get("language") or (src or {}).get("language"),
            "stars": (src or {}).get("stargazers_count", 0),
            "forks": (src or {}).get("forks_count", 0),
        }
        for theme in ("dark", "light"):
            dest = args.out / f"card-{entry['id']}-{theme}.svg"
            dest.write_text(render_project(card, theme), encoding="utf-8")
        print(f"wrote card-{entry['id']}-*.svg  "
              f"({'live' if src else 'static'}, {card['language'] or 'no language'})")


if __name__ == "__main__":
    main()
