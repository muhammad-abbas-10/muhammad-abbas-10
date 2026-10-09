#!/usr/bin/env python3
"""Generate the animated GitHub profile banners.

Run from the repository root:
    python scripts/banner/generate.py

To swap the photo used in the face state of VISUAL.MAP (crop is left,top,right,bottom
in source pixels; omit --crop to use the default):
    python scripts/banner/generate.py --photo path/to/photo.png [--crop 95,312,455,720]

Animation loop (see TIMES): photo (particle portrait + scan overlay) -> workflow
-> network -> </> -> chat -> photo.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
BANNER_VERSION = "v2"  # bump when regenerating, then update the README/preview paths
LOGOS = Path(__file__).resolve().parent / "logos"
DATA = Path(__file__).resolve().parent / "data"

W, H = 1180, 610
# Phase boundaries (seconds), one entry per keyframe:
#   photo hold, photo -> workflow, workflow hold, workflow -> network, network hold,
#   network -> code, code hold, code -> chat, chat hold, chat -> photo.
TIMES = [0, 3.0, 4.4, 7.0, 8.3, 9.9, 11.2, 12.8, 14.1, 15.7, 17.0]
LOOP_SECONDS = TIMES[-1]
INTRO_SECONDS = 3.2
TRAVELLER_COUNT = 900
SEED = 314159

# --- photo state -------------------------------------------------------------
# The working copy of the photo (a face crop, 2x the dot lattice) is committed so
# the banner can be regenerated without the original full-size photo. Replace it
# with:  python scripts/banner/generate.py --photo path/to/photo.png
LATTICE = (300, 340)          # dot lattice, same size as the hero layer
LATTICE_ORIGIN = (74, 154)    # where the lattice sits inside the banner
PHOTO_CROP = (95, 312, 455, 720)   # default crop of the source photo: l, t, r, b
PHOTO_WORK = ROOT / "assets/source/photo-crop.png"
PHOTO_META = ROOT / "assets/source/photo-landmarks.json"   # scan-mesh overlay data
PHOTO_TONE = {
    # target = approximate number of lit dots. gamma > 1 deepens shadows,
    # floor keeps dark regions faintly textured, local_k is local-contrast gain.
    "dark": {"target": 16000, "gamma": 1.5, "floor": 0.05, "local_k": 1.2},
    "light": {"target": 13000, "gamma": 1.0, "floor": 0.10, "local_k": 1.1},
}
PHOTO_BANDS = 48              # random point groups that scatter / assemble

ROWS = [
    ("Subject", "Muhammad Abbas Bajwa"),
    ("Role", "Computer Science Student"),
    ("Education", "FAST NUCES Peshawar"),
    ("Focus", "AI · Agentic Systems · Automation"),
    ("Interests", "Software Development · Open Source"),
    ("Status", "Building + Learning + Shipping"),
    ("Projects", "Pizza Bot · Cafe POS · Receipt Reconciler"),
    ("Experience", "Intern @ Virtiqo"),
    ("Stack.Lang", "Python · C++ · JavaScript · SQL"),
    ("Stack.Frontend", "React · Vite · HTML · CSS"),
    ("Stack.Backend", "Node.js · Express · REST APIs"),
    ("Stack.Database", "PostgreSQL · Neon"),
    ("Stack.Infra", "Vercel · Git · GitHub"),
    ("Stack.Automation", "n8n · WhatsApp Business API"),
    ("Stack.AI", "LLM / AI APIs"),
    ("Grid.GitHub", "muhammad-abbas-10"),
]

THEMES = {
    "dark": {
        "bg": "#0A101F",
        "panel": "#0D1628",
        "panel2": "#101B30",
        "line": "#25344C",
        "muted": "#8291A8",
        "text": "#DDE7F5",
        "hero": "#AA9BEF",
        "chrome": "#22D3EE",
        "accent": "#10B981",
        "shadow": "#02050B",
    },
    "light": {
        "bg": "#F6F8FA",
        "panel": "#FFFFFF",
        "panel2": "#EDF3F7",
        "line": "#CBD7E1",
        "muted": "#64748B",
        "text": "#172033",
        "hero": "#4A3D7A",
        "chrome": "#0891B2",
        "accent": "#10B981",
        "shadow": "#AAB7C4",
    },
}


def make_logos() -> dict[str, Image.Image]:
    """Create clean 400px black-on-transparent silhouette sources.

    All three marks are generic, original shapes (no brand logos).
    """
    LOGOS.mkdir(parents=True, exist_ok=True)
    size = 400
    logos: dict[str, Image.Image] = {}

    # Agent / network graph: a hub node wired to six satellite nodes, joined by a
    # ring. Kept sparse so it still reads clearly when sampled to ~900 dots.
    agent = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(agent)
    hub = np.array([200.0, 200.0])
    sats = [
        hub + 138 * np.array([math.cos(-math.pi / 2 + i * math.tau / 6),
                              math.sin(-math.pi / 2 + i * math.tau / 6)])
        for i in range(6)
    ]
    for p in sats:  # spokes
        d.line([tuple(hub), tuple(p)], fill="black", width=16)
    for p, q in zip(sats, sats[1:] + sats[:1]):  # ring
        d.line([tuple(p), tuple(q)], fill="black", width=12)
    d.ellipse((hub[0] - 50, hub[1] - 50, hub[0] + 50, hub[1] + 50), fill="black")
    for x, y in sats:
        d.ellipse((x - 31, y - 31, x + 31, y + 31), fill="black")
    logos["agent"] = agent

    # </> mark built from broad, rounded strokes.
    code = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(code)
    stroke = 42
    d.line([(154, 95), (66, 200), (154, 305)], fill="black", width=stroke, joint="curve")
    d.line([(246, 95), (334, 200), (246, 305)], fill="black", width=stroke, joint="curve")
    d.line([(225, 72), (174, 328)], fill="black", width=stroke)
    logos["code"] = code

    # Outlined chat bubble with three solid typing dots (conversational / bot mark).
    chat = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(chat)
    d.rounded_rectangle((44, 62, 356, 272), radius=66, fill="black")
    d.rounded_rectangle((76, 94, 324, 240), radius=36, fill=(0, 0, 0, 0))
    d.polygon([(108, 258), (92, 344), (198, 266)], fill="black")
    for x in (134, 200, 266):
        d.ellipse((x - 23, 167 - 23, x + 23, 167 + 23), fill="black")
    logos["chat"] = chat

    for name, image in logos.items():
        image.save(LOGOS / f"{name}.png", optimize=True)
    return logos


def floyd_steinberg(gray: np.ndarray) -> np.ndarray:
    """Serpentine 1-bit Floyd-Steinberg diffusion; True means a lit pixel."""
    work = gray.astype(np.float32) / 255.0
    out = np.zeros_like(work, dtype=bool)
    height, width = work.shape
    for y in range(height):
        left_to_right = y % 2 == 0
        xs = range(width) if left_to_right else range(width - 1, -1, -1)
        direction = 1 if left_to_right else -1
        for x in xs:
            old = work[y, x]
            new = 1.0 if old >= 0.5 else 0.0
            out[y, x] = bool(new)
            err = old - new
            nx = x + direction
            if 0 <= nx < width:
                work[y, nx] += err * 7 / 16
            if y + 1 < height:
                if 0 <= x - direction < width:
                    work[y + 1, x - direction] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if 0 <= nx < width:
                    work[y + 1, nx] += err * 1 / 16
    return out


def cubic_points(
    start: tuple[float, float], control_a: tuple[float, float],
    control_b: tuple[float, float], end: tuple[float, float], steps: int = 48,
) -> list[tuple[int, int]]:
    """Return a compact, raster-friendly cubic Bézier path."""
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        x = u ** 3 * start[0] + 3 * u * u * t * control_a[0] + 3 * u * t * t * control_b[0] + t ** 3 * end[0]
        y = u ** 3 * start[1] + 3 * u * u * t * control_a[1] + 3 * u * t * t * control_b[1] + t ** 3 * end[1]
        points.append((round(x), round(y)))
    return points


def make_hero(theme: str = "dark") -> Image.Image:
    """Procedural 300x340 automation workflow for the VISUAL.MAP panel.

    The source is intentionally rasterized and then Floyd-Steinberg dithered by
    ``hero_points`` so it has the same 1-bit particle treatment as the other
    animated states. Returns RGBA; alpha limits the dots to the workflow itself.
    """
    w, h = 300, 340
    dark = theme == "dark"
    interior, connector, edge = (38, 160, 244) if dark else (232, 84, 20)
    art = Image.new("L", (w, h), 0)
    alpha = Image.new("L", (w, h), 0)
    draw, coverage = ImageDraw.Draw(art), ImageDraw.Draw(alpha)

    def line(points: list[tuple[int, int]], fill: int, width: int = 2) -> None:
        draw.line(points, fill=fill, width=width, joint="curve")
        coverage.line(points, fill=255, width=width, joint="curve")

    def polygon(points: list[tuple[int, int]], fill: int) -> None:
        draw.polygon(points, fill=fill)
        coverage.polygon(points, fill=255)

    def node(box: tuple[int, int, int, int]) -> None:
        draw.rounded_rectangle(box, radius=13, fill=interior, outline=edge, width=2)
        coverage.rounded_rectangle(box, radius=13, fill=255)

    # Curved signal paths sit behind the five processing nodes.
    paths = (
        ((104, 151), (64, 151), (125, 98), (91, 82)),
        ((196, 151), (236, 151), (175, 98), (209, 82)),
        ((104, 197), (64, 197), (125, 242), (91, 257)),
        ((196, 197), (236, 197), (175, 242), (209, 257)),
    )
    for path in paths:
        line(cubic_points(*path), connector, 2)

    boxes = {
        "bolt": (10, 10, 94, 94),
        "database": (206, 10, 290, 94),
        "message": (10, 246, 94, 330),
        "play": (206, 246, 290, 330),
        "core": (104, 128, 196, 220),
    }
    for box in boxes.values():
        node(box)

    # Power input.
    polygon([(53, 28), (36, 56), (49, 56), (43, 78), (69, 45), (56, 45)], edge)
    # Data source: three database trays.
    for y in (32, 46, 60):
        draw.rounded_rectangle((228, y, 270, y + 9), radius=3, outline=edge, width=2)
        coverage.rounded_rectangle((228, y, 270, y + 9), radius=3, outline=255, width=2)
        line([(235, y + 4), (263, y + 4)], connector, 1)
    # Message output.
    draw.rounded_rectangle((27, 271, 76, 302), radius=7, outline=edge, width=2)
    coverage.rounded_rectangle((27, 271, 76, 302), radius=7, outline=255, width=2)
    polygon([(43, 301), (39, 311), (53, 302)], edge)
    for x in (39, 51, 63):
        draw.ellipse((x, 284, x + 4, 288), fill=edge)
        coverage.ellipse((x, 284, x + 4, 288), fill=255)
    # Run output.
    polygon([(233, 269), (233, 307), (275, 288)], edge)
    # Central automation/spark node.
    polygon([(150, 143), (159, 165), (182, 174), (160, 183), (150, 208), (141, 184), (118, 174), (140, 165)], edge)
    polygon([(150, 156), (155, 170), (170, 174), (155, 178), (150, 193), (145, 178), (130, 174), (145, 170)], connector)

    # Small signal glints at the points where a path meets a node.
    for x, y in ((96, 82), (204, 82), (96, 257), (204, 257)):
        line([(x - 4, y), (x + 4, y)], edge, 1)
        line([(x, y - 4), (x, y + 4)], edge, 1)

    gray = np.asarray(art, dtype=np.uint8)
    rgba = np.dstack([gray, gray, gray, np.asarray(alpha, dtype=np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def hero_points(theme: str, rng: np.random.Generator) -> np.ndarray:
    """Return sampled x/y banner coordinates from a 300x340 dither grid."""
    crop = make_hero(theme)
    rgb = crop.convert("RGB")
    alpha = np.asarray(crop.getchannel("A"), dtype=np.float32) / 255.0

    if theme == "dark":
        lum = np.asarray(ImageOps.grayscale(rgb), dtype=np.float32)
        prepared = Image.fromarray(np.uint8(np.clip(lum * alpha, 0, 255)), "L")
        select_lit = True
    else:
        white = Image.new("RGBA", crop.size, "white")
        white.alpha_composite(crop)
        prepared = ImageOps.grayscale(white.convert("RGB"))
        select_lit = False

    if theme == "dark":
        mask = Image.fromarray(np.uint8((alpha > 0.08) * 255), "L")
        prepared = ImageOps.equalize(prepared, mask=mask)
    else:
        prepared = ImageOps.autocontrast(prepared, cutoff=1)
    prepared = ImageEnhance.Contrast(prepared).enhance(1.15)
    bits = floyd_steinberg(np.asarray(prepared))
    active = bits if select_lit else ~bits
    if theme == "dark":
        active &= alpha > 0.08
    else:
        active &= alpha > 0.08  # never draw outside the subject on light

    ys, xs = np.where(active)
    if len(xs) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    points = np.column_stack((74 + xs, 154 + ys)).astype(np.float32)
    if len(points) > 18000:
        points = points[rng.choice(len(points), 18000, replace=False)]
    return points


def prepare_photo(source: Path, box: tuple[int, int, int, int] = PHOTO_CROP) -> None:
    """Crop the source photo to the face area and save the committed working copy."""
    image = Image.open(source).convert("RGBA")
    left, top, right, bottom = box
    if abs((right - left) / (bottom - top) - LATTICE[0] / LATTICE[1]) > 0.03:
        print("warning: crop aspect differs from the 300x340 lattice; the face will be stretched")
    crop = image.crop(box).resize((LATTICE[0] * 2, LATTICE[1] * 2), Image.Resampling.LANCZOS)
    PHOTO_WORK.parent.mkdir(parents=True, exist_ok=True)
    crop.save(PHOTO_WORK, optimize=True)
    print(f"{PHOTO_WORK.relative_to(ROOT)}: saved {crop.size[0]}x{crop.size[1]} working crop")


def photo_points(theme: str) -> np.ndarray:
    """Turn the face crop into a 1-bit Floyd-Steinberg particle field.

    Steps: subject-only luminance -> local contrast (so eyes, brows and the beard
    line separate from flat skin) -> tone curve -> global density calibration so
    roughly PHOTO_TONE[theme]["target"] dots are lit -> serpentine dither.
    Dark banner: dots light up where the photo is bright. Light banner: dots are
    "ink" where it is dark. Dots only ever fall inside the subject's alpha mask.
    """
    cfg = PHOTO_TONE[theme]
    image = Image.open(PHOTO_WORK).convert("RGBA").resize(LATTICE, Image.Resampling.LANCZOS)
    mask = np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0 > 0.5
    lum = np.asarray(ImageOps.grayscale(image.convert("RGB")), dtype=np.float32) / 255.0
    lum = np.where(mask, lum, lum[mask].mean())  # empty area must not bias contrast

    blur = np.asarray(
        Image.fromarray(np.uint8(lum * 255)).filter(ImageFilter.GaussianBlur(14)),
        dtype=np.float32,
    ) / 255.0
    local = np.clip(lum + cfg["local_k"] * (lum - blur), 0.0, 1.0)
    local = np.asarray(
        ImageOps.autocontrast(Image.fromarray(np.uint8(local * 255)), cutoff=1),
        dtype=np.float32,
    ) / 255.0
    tone = local if theme == "dark" else 1.0 - local
    tone = cfg["floor"] + (1.0 - cfg["floor"]) * tone ** cfg["gamma"]

    # Soft elliptical falloff around the face: shoulders and collar thin out into
    # scattered particles instead of ending at the lattice edge (no hard rectangle).
    yy, xx = np.mgrid[0:LATTICE[1], 0:LATTICE[0]].astype(np.float32)
    radius = np.sqrt(((xx - 150.0) / 165.0) ** 2 + ((yy - 135.0) / 215.0) ** 2)
    fade = np.clip((1.0 - radius) / 0.35, 0.0, 1.0)
    tone = tone * (fade * fade * (3.0 - 2.0 * fade))

    # Dither output density equals mean tone, so bisect a global gain to hit the target.
    low, high = 0.02, 1.0
    bits = np.zeros_like(mask)
    for _ in range(9):
        gain = (low + high) / 2
        bits = floyd_steinberg(np.uint8(np.clip(tone * gain, 0.0, 1.0) * 255)) & mask
        if bits.sum() > cfg["target"]:
            high = gain
        else:
            low = gain
    ys, xs = np.where(bits)
    return np.column_stack((LATTICE_ORIGIN[0] + xs, LATTICE_ORIGIN[1] + ys)).astype(np.float32)


def face_overlay(meta: dict | None, theme: dict, cyc: str) -> list[str]:
    """Scan line + face-mesh overlay, visible during the opening photo hold."""
    ox, oy = LATTICE_ORIGIN
    chrome = theme["chrome"]
    ft = lambda ts: ";".join(f"{v / LOOP_SECONDS:.4f}".rstrip("0").rstrip(".") for v in ts)
    out: list[str] = []

    # Scan line: a soft band whose leading edge sweeps the lattice top to bottom.
    out.append(
        '<g opacity="0" shape-rendering="auto">'
        f'<animate attributeName="opacity" {cyc} keyTimes="{ft([0, 0.3, 0.5, 2.7, 2.9, LOOP_SECONDS])}" '
        'values="0;0;1;1;0;0"/>'
        f'<animateTransform attributeName="transform" {cyc} keyTimes="{ft([0, 0.4, 2.8, LOOP_SECONDS])}" '
        f'values="0 0;0 0;0 {LATTICE[1]};0 {LATTICE[1]}"/>'
        f'<rect x="{ox}" y="{oy - 26}" width="{LATTICE[0]}" height="26" fill="url(#scan)"/>'
        f'<rect x="{ox}" y="{oy - 1}" width="{LATTICE[0]}" height="1.4" fill="{chrome}" opacity=".9"/>'
        "</g>"
    )

    if not meta:
        return out
    pts = {k: (ox + v[0], oy + v[1]) for k, v in meta["points"].items()}
    x0, y0, x1, y1 = (meta["bracket"][0] + ox, meta["bracket"][1] + oy,
                      meta["bracket"][2] + ox, meta["bracket"][3] + oy)
    arm = 14
    bracket = (f"M{x0} {y0 + arm}v-{arm}h{arm}M{x1 - arm} {y0}h{arm}v{arm}"
               f"M{x1} {y1 - arm}v{arm}h-{arm}M{x0 + arm} {y1}h-{arm}v-{arm}")
    edges = "".join(f"M{pts[a][0]} {pts[a][1]}L{pts[b][0]} {pts[b][1]}" for a, b in meta["mesh"])
    marks = "".join(f"M{x - 5} {y}h10M{x} {y - 5}v10" for x, y in pts.values())
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="1.8"/>' for x, y in pts.values())
    out.append(
        '<g opacity="0" shape-rendering="auto">'
        f'<animate attributeName="opacity" {cyc} keyTimes="{ft([0, 0.5, 1.0, 2.5, 3.0, LOOP_SECONDS])}" '
        'values="0;0;1;1;0;0"/>'
        f'<path d="{edges}" fill="none" stroke="{chrome}" stroke-width=".9" opacity=".75"/>'
        f'<path d="{marks}" fill="none" stroke="{chrome}" stroke-width="1" opacity=".9"/>'
        f'<g fill="{chrome}">{dots}</g>'
        f'<path d="{bracket}" fill="none" stroke="{chrome}" stroke-width="1.4"/>'
        f'<text x="{x0 + 6}" y="{y0 + 15}" fill="{chrome}" font-size="9" letter-spacing="1" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace">FACE.MAP</text>'
        "</g>"
    )
    return out


def sample_logo_points(
    image: Image.Image, rng: np.random.Generator, count: int
) -> np.ndarray:
    """Sample a silhouette into the hero frame's visual coordinate space."""
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha > 127)
    chosen = rng.choice(len(xs), count, replace=len(xs) < count)
    # Logo occupies a centered 270x270 square inside VISUAL.MAP.
    return np.column_stack((89 + xs[chosen] * 0.675, 188 + ys[chosen] * 0.675)).astype(
        np.float32
    )


def transport(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Order target points by minimum-cost assignment from source points."""
    rows, cols = linear_sum_assignment(cdist(source, target, metric="sqeuclidean"))
    ordered = np.empty_like(target)
    ordered[rows] = target[cols]
    return ordered


def num(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def point_path(points: np.ndarray) -> str:
    """Aggregate adjacent horizontal one-pixel dots into compact SVG path runs."""
    if not len(points):
        return ""
    integer = np.rint(points).astype(int)
    unique = sorted({(int(x), int(y)) for x, y in integer}, key=lambda p: (p[1], p[0]))
    chunks: list[str] = []
    i = 0
    while i < len(unique):
        x0, y = unique[i]
        x1 = x0
        i += 1
        while i < len(unique) and unique[i][1] == y and unique[i][0] <= x1 + 1:
            x1 = unique[i][0]
            i += 1
        chunks.append(f"M{x0} {y}h{x1 - x0 + 1}")
    return "".join(chunks)


def dotted_leader(x1: float, x2: float, y: float) -> str:
    if x2 <= x1:
        return ""
    return "".join(f"M{x} {num(y)}h1" for x in np.arange(x1, x2, 5.0))


def text_width(text: str, font_size: float) -> float:
    """Stable monospace width used both for textLength and leader placement."""
    return len(text) * font_size * 0.605


def animate_values(points: list[np.ndarray], index: int) -> str:
    return ";".join(f"{num(p[index, 0])} {num(p[index, 1])}" for p in points)


def render_svg(
    theme_name: str,
    hero: np.ndarray,
    logo_points: dict[str, np.ndarray],
    rng: np.random.Generator,
    photo: np.ndarray,
    meta: dict | None = None,
) -> str:
    t = THEMES[theme_name]
    n = min(TRAVELLER_COUNT, len(hero), len(photo))
    source = photo[rng.choice(len(photo), n, replace=False)]
    workflow = transport(source, hero[rng.choice(len(hero), n, replace=False)])
    # Travellers ride the whole chain: photo -> workflow -> network -> code -> chat -> photo.
    # Every morph uses optimal transport, so particles take their shortest paths.
    agent = transport(workflow, logo_points["agent"][:n])
    code = transport(agent, logo_points["code"][:n])
    chat = transport(code, logo_points["chat"][:n])

    def frac(v: float) -> str:
        return f"{v / LOOP_SECONDS:.4f}".rstrip("0").rstrip(".")

    key_times = ";".join(frac(v) for v in TIMES)
    cyc = f'begin="{INTRO_SECONDS}s" dur="{num(LOOP_SECONDS)}s" repeatCount="indefinite"'
    # Returning each traveller to its exact starting photo coordinate keeps the
    # repeat boundary seamless.
    frames = [source, source, workflow, workflow, agent, agent, code, code, chat, chat, source]

    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
        'aria-labelledby="title desc">',
        "<title id=\"title\">Muhammad Abbas Bajwa's live system profile</title>",
        '<desc id="desc">Animated terminal profile with a dithered automation workflow that '
        "morphs into a particle portrait, then neural-network, code, and chat silhouettes.</desc>",
        "<defs>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">'
        f'<feDropShadow dx="0" dy="12" stdDeviation="16" flood-color="{t["shadow"]}" '
        'flood-opacity=".28"/></filter>',
        '<filter id="glow" x="-100%" y="-100%" width="300%" height="300%">'
        f'<feGaussianBlur stdDeviation="3" result="b"/><feFlood flood-color="{t["chrome"]}" '
        'flood-opacity=".35"/><feComposite in2="b" operator="in"/>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '<clipPath id="visualClip"><rect x="49" y="124" width="390" height="414" rx="3"/></clipPath>',
        '<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{t["chrome"]}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{t["chrome"]}" stop-opacity=".38"/></linearGradient>',
        "</defs>",
        f'<rect width="{W}" height="{H}" rx="18" fill="{t["bg"]}"/>',
        f'<rect x="13" y="13" width="1154" height="584" rx="13" fill="{t["panel"]}" '
        f'stroke="{t["line"]}" filter="url(#shadow)"/>',
        f'<path d="M13 62H1167" stroke="{t["line"]}"/>',
        '<circle cx="38" cy="38" r="6" fill="#FF5F57"/>'
        '<circle cx="59" cy="38" r="6" fill="#FEBC2E"/>'
        '<circle cx="80" cy="38" r="6" fill="#28C840"/>',
        f'<text x="590" y="43" text-anchor="middle" fill="{t["muted"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
        'letter-spacing=".4">profile.sh --live</text>',
        # Left visual frame.
        f'<rect x="35" y="88" width="418" height="472" rx="6" fill="{t["panel2"]}" '
        f'stroke="{t["line"]}"/>',
        f'<path d="M35 124H453" stroke="{t["line"]}"/>',
        f'<text x="49" y="111" fill="{t["chrome"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
        'font-weight="700" letter-spacing="1.2">VISUAL.MAP</text>',
        f'<text x="438" y="111" text-anchor="end" fill="{t["muted"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">300×340 / 1-BIT</text>',
        f'<path d="M49 141h12M49 141v12M439 141h-12M439 141v12M49 539h12M49 539v-12'
        f'M439 539h-12M439 539v-12" fill="none" stroke="{t["chrome"]}" opacity=".55"/>',
        '<g clip-path="url(#visualClip)" shape-rendering="crispEdges">',
        # The photo layer is visible at t=0, so static first frames (and renderers
        # without SMIL) open on the portrait; the workflow starts hidden.
    ]

    # 1) Workflow layer: it assembles after the opening photo and then dissolves
    #    toward the network state.
    agent_centroid = agent.mean(axis=0)
    band_ids = rng.integers(0, 94, size=len(hero))
    noise = rng.normal(0, 4, size=(94, 2))
    workflow_opacity = ";".join(["0", "0", ".94", ".94", "0"] + ["0"] * 6)
    for band in range(94):
        pts = hero[band_ids == band]
        if not len(pts):
            continue
        delta = (agent_centroid - pts.mean(axis=0)) * 0.18 + noise[band]
        away = f"{num(delta[0])} {num(delta[1])}"
        moves = ";".join(["0 0"] * 4 + [away, away] + ["0 0"] * 5)
        parts.append(
            f'<path d="{point_path(pts)}" fill="none" stroke="{t["hero"]}" stroke-width="1" '
            'opacity="0">'
            f'<animateTransform attributeName="transform" {cyc} keyTimes="{key_times}" '
            f'values="{moves}"/>'
            f'<animate attributeName="opacity" {cyc} keyTimes="{key_times}" '
            f'values="{workflow_opacity}"/></path>'
        )

    # 2) Photo layer: visible from the first frame, it fades toward the workflow,
    #    stays hidden during the intermediate states, then returns at the loop end.
    workflow_centroid = workflow.mean(axis=0)
    band_of = rng.integers(0, PHOTO_BANDS, size=len(photo))
    drift_noise = rng.normal(0, 4, size=(PHOTO_BANDS, 2))
    for band in range(PHOTO_BANDS):
        pts = photo[band_of == band]
        if not len(pts):
            continue
        off = (workflow_centroid - pts.mean(axis=0)) * 0.18 + drift_noise[band]
        gone = f"{num(off[0])} {num(off[1])}"
        times = [0, TIMES[1], TIMES[2], TIMES[9], LOOP_SECONDS]
        parts.append(
            f'<path d="{point_path(pts)}" fill="none" stroke="{t["hero"]}" stroke-width="1" '
            'opacity=".94">'
            f'<animateTransform attributeName="transform" {cyc} '
            f'keyTimes="{";".join(frac(v) for v in times)}" '
            f'values="0 0;0 0;{gone};{gone};0 0"/>'
            f'<animate attributeName="opacity" {cyc} '
            f'keyTimes="{";".join(frac(v) for v in times)}" values=".94;.94;0;0;.94"/></path>'
        )

    # 3) Travellers: optimal-transport particles, tiny path squares (never glyphs).
    #    They share one opacity timeline, so it lives on the group, not on each dot.
    parts.append(
        f'<g fill="{t["hero"]}" opacity="0">'
        f'<animate attributeName="opacity" {cyc} keyTimes="{key_times}" '
        'values="0;0;.8;.8;1;1;1;1;1;1;0"/>'
    )
    for i in range(n):
        values = ";".join(f"{round(f[i, 0])} {round(f[i, 1])}" for f in frames)
        parts.append(
            '<path d="M-.65-.65h1.3v1.3h-1.3z">'
            f'<animateTransform attributeName="transform" {cyc} keyTimes="{key_times}" '
            f'values="{values}"/></path>'
        )
    parts.append("</g>")

    # 4) Scan line + face mesh, only during the photo state.
    parts.extend(face_overlay(meta, t, cyc))

    parts.extend(
        [
            "</g>",
            # Small frame telemetry.
            f'<text x="58" y="551" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">'
            f'PTS {len(hero):05d} · FS/SERPENTINE</text>',
            # Right information panel.
            f'<rect x="474" y="88" width="672" height="472" rx="6" fill="{t["panel2"]}" '
            f'stroke="{t["line"]}"/>',
            f'<path d="M474 124H1146" stroke="{t["line"]}"/>',
            f'<text x="490" y="111" fill="{t["chrome"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
            'font-weight="700" letter-spacing="1.2">SYSTEM.INFO</text>',
            # LIVE badge and handle pill.
            '<g filter="url(#glow)"><circle cx="878" cy="106" r="4" fill="#FF4D5A">'
            '<animate attributeName="opacity" values="1;.3;1" dur="1.6s" repeatCount="indefinite"/>'
            '</circle></g>',
            '<text x="890" y="111" fill="#FF4D5A" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="12" '
            'font-weight="700">LIVE</text>',
            f'<rect x="946" y="94" width="182" height="24" rx="12" fill="{t["chrome"]}" opacity=".16" '
            f'stroke="{t["chrome"]}"/>',
            f'<text x="1037" y="111" text-anchor="middle" fill="{t["chrome"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="14" '
            'font-weight="700">@muhammad-abbas-10</text>',
        ]
    )

    value_right = 1127.0
    row_y = 153.0
    for label, value in ROWS:
        value_len = text_width(value, 14)
        label_len = text_width(label, 14)
        leader_start = 491 + label_len + 12
        leader_end = value_right - value_len - 12
        parts.extend(
            [
                f'<text x="491" y="{num(row_y)}" fill="{t["muted"]}" '
                'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="14">'
                f"{html.escape(label)}</text>",
                f'<path d="{dotted_leader(leader_start, leader_end, row_y - 4)}" '
                f'fill="none" stroke="{t["line"]}" stroke-width="1" shape-rendering="crispEdges"/>',
                f'<text x="{num(value_right)}" y="{num(row_y)}" text-anchor="end" '
                f'fill="{t["text"]}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" '
                f'font-size="14" textLength="{num(value_len)}" lengthAdjust="spacingAndGlyphs">'
                f"{html.escape(value)}</text>",
            ]
        )
        row_y += 23

    parts.extend(
        [
            f'<path d="M490 530H1130" stroke="{t["line"]}"/>',
            f'<text x="491" y="548" fill="{t["accent"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">'
            "● ALL SYSTEMS NOMINAL</text>",
            f'<text x="1128" y="548" text-anchor="end" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">'
            "FAST NUCES · CS STUDENT</text>",
            "</svg>",
        ]
    )
    return "".join(parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the animated profile banners.")
    parser.add_argument("--photo", type=Path, metavar="FILE",
                        help="re-crop this photo into assets/source/photo-crop.png first")
    parser.add_argument("--crop", metavar="L,T,R,B", default=None,
                        help="crop box for --photo in source pixels "
                             f"(default {','.join(map(str, PHOTO_CROP))})")
    args = parser.parse_args(argv)

    if args.photo:
        box = tuple(int(v) for v in args.crop.split(",")) if args.crop else PHOTO_CROP
        prepare_photo(args.photo, box)
        if PHOTO_META.exists():
            print(f"note: {PHOTO_META.relative_to(ROOT)} holds face-mesh landmarks for the "
                  "default photo; edit or delete it if your new photo is different")
    if not PHOTO_WORK.exists():
        raise SystemExit(f"Missing {PHOTO_WORK.relative_to(ROOT)}: "
                         "run  python scripts/banner/generate.py --photo path/to/photo.png")
    meta = json.loads(PHOTO_META.read_text(encoding="utf-8")) if PHOTO_META.exists() else None

    ASSETS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    logos = make_logos()

    # Cache theme-specific dither points as reproducible source data.
    heroes: dict[str, np.ndarray] = {}
    photos: dict[str, np.ndarray] = {}
    for index, theme in enumerate(THEMES):
        rng = np.random.default_rng(SEED + index)
        heroes[theme] = hero_points(theme, rng)
        np.save(DATA / f"hero-{theme}.npy", heroes[theme])
        photos[theme] = photo_points(theme)
        np.save(DATA / f"photo-{theme}.npy", photos[theme])

    for index, theme in enumerate(THEMES):
        rng = np.random.default_rng(SEED + 100 + index)
        sampled = {
            name: sample_logo_points(image, rng, TRAVELLER_COUNT)
            for name, image in logos.items()
        }
        for name, points in sampled.items():
            np.save(DATA / f"{name}-{theme}.npy", points)
        svg = render_svg(theme, heroes[theme], sampled, rng, photos[theme], meta)
        output = ASSETS / f"banner-{theme}.{BANNER_VERSION}.svg"
        output.write_text(svg, encoding="utf-8")
        byte_size = output.stat().st_size
        print(
            f"{output.relative_to(ROOT)}: {byte_size:,} bytes "
            f"({byte_size / 1024:.1f} KiB), {len(heroes[theme]):,} hero dots, "
            f"{len(photos[theme]):,} photo dots, {TRAVELLER_COUNT} travellers"
        )

    for name in logos:
        output = LOGOS / f"{name}.png"
        print(f"{output.relative_to(ROOT)}: {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
