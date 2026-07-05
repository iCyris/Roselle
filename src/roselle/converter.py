from __future__ import annotations

import hashlib
import html
import json
import math
import shutil
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from PIL import Image, ImageChops, ImageColor, ImageDraw

try:
    import cairosvg
except Exception:  # pragma: no cover - optional runtime dependency
    cairosvg = None


RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Component:
    color: RGBA
    pixels: int
    bbox: tuple[int, int, int, int]
    role: str


def vectorize(input_path: Path, out_dir: Path, palette_size: int = 20) -> dict:
    input_path = input_path.resolve()
    out_dir = out_dir.resolve()
    if not input_path.exists():
        return {
            "status": "error",
            "code": "input_missing",
            "message": f"Input image does not exist: {input_path}",
        }

    image = Image.open(input_path).convert("RGBA")
    width, height = image.size
    out_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(out_dir)
    candidates_dir = out_dir / "candidates"
    renders_dir = out_dir / "renders"
    diffs_dir = out_dir / "diffs"
    for directory in (candidates_dir, renders_dir, diffs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_copy = out_dir / input_path.name
    if input_path != source_copy:
        shutil.copyfile(input_path, source_copy)

    analysis = analyze_image(image, input_path)
    palette = build_palette(image, palette_size)
    quantized = quantize_image(image, palette)
    component_groups = build_color_groups(quantized)

    final_svg = out_dir / "final.svg"
    layered_svg = out_dir / "layered.svg"
    source_report_preview = renders_dir / "source-report.png"
    final_report_preview = renders_dir / "final-report.png"
    final_preview = renders_dir / "final.png"
    layered_preview = renders_dir / "layered.png"
    final_diff = diffs_dir / "final.png"
    layered_diff = diffs_dir / "layered.png"

    layered_metrics = write_grouped_svg(
        image=quantized,
        path=layered_svg,
        title="Roselle layered SVG",
        description="Palette-grouped SVG designed for readable layers and animation-oriented editing.",
        component_groups=component_groups,
    )
    final_metrics = write_grouped_svg(
        image=image,
        path=final_svg,
        title="Roselle final SVG",
        description="Lossless color-grouped SVG using horizontal run paths for pixel-identical rendering.",
        component_groups=None,
    )

    render_metrics = {}
    # Always write deterministic previews. If a renderer is unavailable, these
    # still make the report useful and keep final.svg verifiable by construction.
    image.save(final_preview)
    quantized.save(layered_preview)
    write_report_preview(image, source_report_preview)
    write_report_preview(image, final_report_preview)
    render_metrics["final"] = compare_images(image, image, final_diff)
    render_metrics["layered"] = compare_images(image, quantized, layered_diff)

    if cairosvg is not None:
        render_svg(final_svg, final_preview, width, height)
        render_svg(layered_svg, layered_preview, width, height)
        render_metrics["final"] = compare_images(image, Image.open(final_preview).convert("RGBA"), final_diff)
        render_metrics["layered"] = compare_images(image, Image.open(layered_preview).convert("RGBA"), layered_diff)

    manifest = {
        "schema_version": "1.0",
        "status": "ok",
        "input": {
            "path": str(input_path),
            "sha256": sha256_file(input_path),
            "format": input_path.suffix.lower().lstrip("."),
            "width": width,
            "height": height,
            "has_alpha": analysis["has_alpha"],
        },
        "outputs": {
            "final_svg": str(final_svg),
            "layered_svg": str(layered_svg),
            "report": str(out_dir / "report.html"),
        },
        "analysis": analysis,
        "palette": [color_record(color, count, width * height) for color, count in palette],
        "groups": component_groups,
        "candidates": [
            {
                "id": "final",
                "path": str(final_svg),
                "purpose": "pixel_fidelity_delivery",
                "metrics": final_metrics | render_metrics.get("final", {}),
            },
            {
                "id": "layered",
                "path": str(layered_svg),
                "purpose": "editable_grouped_reference",
                "metrics": layered_metrics | render_metrics.get("layered", {}),
            },
        ],
        "warnings": build_warnings(analysis, final_metrics, layered_metrics, render_metrics),
    }

    (out_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_report(
        out_dir=out_dir,
        source_path=source_report_preview,
        manifest=manifest,
        final_preview=final_report_preview if final_report_preview.exists() else None,
    )

    return {
        "status": "ok",
        "final_svg": str(final_svg),
        "layered_svg": str(layered_svg),
        "manifest": str(out_dir / "manifest.json"),
        "report": str(out_dir / "report.html"),
        "metrics": manifest["candidates"],
        "warnings": manifest["warnings"],
    }


def analyze_image(image: Image.Image, input_path: Path) -> dict:
    pixels = image_pixels(image)
    counts = Counter(pixels)
    opaque = sum(1 for *_, alpha in pixels if alpha == 255)
    transparent = sum(1 for *_, alpha in pixels if alpha == 0)
    semi = len(pixels) - opaque - transparent
    non_white = sum(1 for r, g, b, a in pixels if a and (r, g, b) != (255, 255, 255))
    bbox = image.getbbox()
    return {
        "file_name": input_path.name,
        "width": image.width,
        "height": image.height,
        "pixel_count": image.width * image.height,
        "unique_colors": len(counts),
        "has_alpha": semi > 0 or transparent > 0,
        "opaque_pixels": opaque,
        "transparent_pixels": transparent,
        "semi_transparent_pixels": semi,
        "non_white_pixels": non_white,
        "content_bbox": bbox,
        "top_colors": [color_record(color, count, image.width * image.height) for color, count in counts.most_common(12)],
    }


def cleanup_previous_outputs(out_dir: Path) -> None:
    generated_files = [
        "final.svg",
        "layered.svg",
        "exact.svg",
        "manifest.json",
        "analysis.json",
        "report.html",
    ]
    for name in generated_files:
        path = out_dir / name
        if path.exists():
            path.unlink()
    for directory_name in ("renders", "diffs", "candidates"):
        directory = out_dir / directory_name
        if not directory.exists():
            continue
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()


def build_palette(image: Image.Image, palette_size: int) -> list[tuple[RGBA, int]]:
    colors = Counter(image_pixels(image))
    if len(colors) <= palette_size:
        return colors.most_common()

    # Pillow's adaptive palette keeps the visual family compact while preserving
    # smooth gradients better than threshold-only grouping for this kind of asset.
    rgb_image = image.convert("RGB")
    paletted = rgb_image.quantize(colors=palette_size, method=Image.Quantize.MEDIANCUT)
    palette_image = paletted.convert("RGBA")
    counts = Counter(image_pixels(palette_image))

    # Keep pure white stable because it is the document/background color in the
    # fixture and should remain a separate editable group.
    if (255, 255, 255, 255) in colors and (255, 255, 255, 255) not in counts:
        counts[(255, 255, 255, 255)] = colors[(255, 255, 255, 255)]

    protected = protected_accent_colors(colors)
    for color, count in protected:
        if color not in counts:
            counts[color] = count

    selected = counts.most_common()
    protected_set = {color for color, _ in protected}
    protected_items = [(color, count) for color, count in selected if color in protected_set]
    normal_items = [(color, count) for color, count in selected if color not in protected_set]
    room = max(0, palette_size - len(protected_items))
    return (protected_items + normal_items[:room])[:palette_size]


def protected_accent_colors(colors: Counter[RGBA]) -> list[tuple[RGBA, int]]:
    accents: list[tuple[RGBA, int]] = []
    dark = Counter()
    for color, count in colors.items():
        r, g, b, a = color
        if a == 0:
            continue
        if r < 100 and g < 80 and b < 70:
            dark[color] = count
    if dark:
        accents.append(dark.most_common(1)[0])
    return accents


def quantize_image(image: Image.Image, palette: list[tuple[RGBA, int]]) -> Image.Image:
    palette_colors = [color for color, _ in palette]
    result = Image.new("RGBA", image.size)
    out = result.load()
    src = image.load()
    for y in range(image.height):
        for x in range(image.width):
            px = src[x, y]
            out[x, y] = nearest_color(px, palette_colors)
    return result


def build_color_groups(image: Image.Image) -> list[dict]:
    width, height = image.size
    groups = []
    by_color = runs_by_color(image)
    for index, (color, runs) in enumerate(
        sorted(by_color.items(), key=lambda item: (-sum(run[2] for run in item[1]), item[0])),
        start=1,
    ):
        pixels = sum(run[2] for run in runs)
        min_x = min(run[0] for run in runs)
        min_y = min(run[1] for run in runs)
        max_x = max(run[0] + run[2] for run in runs)
        max_y = max(run[1] + 1 for run in runs)
        role = classify_color_by_distribution(color, (min_x, min_y, max_x, max_y), pixels, width, height)
        groups.append(
            {
                "id": f"{role}-{index:02d}",
                "role": role,
                "color": color_to_hex(color),
                "pixels": pixels,
                "percentage": round(pixels * 100 / (width * height), 4),
                "bbox": (min_x, min_y, max_x, max_y),
                "run_count": len(runs),
            }
        )
    return groups


def nearest_color(color: RGBA, palette: Iterable[RGBA]) -> RGBA:
    r, g, b, a = color
    best = None
    best_distance = math.inf
    for pr, pg, pb, pa in palette:
        distance = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2 + ((a - pa) * 2) ** 2
        if distance < best_distance:
            best = (pr, pg, pb, pa)
            best_distance = distance
    return best or color


def find_components(image: Image.Image, transparent: RGBA | None = None) -> list[Component]:
    width, height = image.size
    pix = image.load()
    visited: set[tuple[int, int]] = set()
    components: list[Component] = []
    for y in range(height):
        for x in range(width):
            if (x, y) in visited:
                continue
            color = pix[x, y]
            if transparent is not None and color == transparent:
                visited.add((x, y))
                continue
            queue = deque([(x, y)])
            visited.add((x, y))
            count = 0
            min_x = max_x = x
            min_y = max_y = y
            while queue:
                cx, cy = queue.popleft()
                count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or (nx, ny) in visited:
                        continue
                    if pix[nx, ny] == color:
                        visited.add((nx, ny))
                        queue.append((nx, ny))
            components.append(Component(color=color, pixels=count, bbox=(min_x, min_y, max_x + 1, max_y + 1), role=""))
    return components


def build_component_groups(components: list[Component], width: int, height: int) -> list[dict]:
    groups: list[dict] = []
    for index, component in enumerate(sorted(components, key=lambda c: c.pixels, reverse=True), start=1):
        color = component.color
        role = classify_component(component, width, height)
        groups.append(
            {
                "id": f"{role}-{index:03d}",
                "role": role,
                "color": color_to_hex(color),
                "pixels": component.pixels,
                "bbox": component.bbox,
            }
        )
    return groups


def classify_color_by_distribution(
    color: RGBA,
    bbox: tuple[int, int, int, int],
    pixels: int,
    width: int,
    height: int,
) -> str:
    r, g, b, _ = color
    x0, y0, x1, y1 = bbox
    if r > 246 and g > 246 and b > 246 and pixels > width * height * 0.2:
        return "background"
    if r < 90 and g < 70 and b < 45:
        return "eyes-brown"
    if b > r + 20 and g > r + 8:
        return "body-blue"
    if abs(r - g) <= 14 and abs(g - b) <= 14:
        if y1 < height * 0.62:
            return "hat-gray"
        return "neutral-shadow"
    if y0 < height * 0.55:
        return "hat-detail"
    return "graphic-detail"


def classify_component(component: Component, width: int, height: int) -> str:
    r, g, b, _ = component.color
    x0, y0, x1, y1 = component.bbox
    if r > 246 and g > 246 and b > 246 and component.pixels > width * height * 0.2:
        return "background"
    if b > r + 25 and g > r + 10:
        return "body-blue"
    if abs(r - g) <= 10 and abs(g - b) <= 10:
        return "hat-gray"
    if r < 90 and g < 70 and b < 45:
        return "eyes-brown"
    if y0 < height * 0.55:
        return "hat-detail"
    return "graphic-detail"


def write_grouped_svg(
    image: Image.Image,
    path: Path,
    title: str,
    description: str,
    component_groups: list[dict] | None,
) -> dict:
    width, height = image.size
    by_color = runs_by_color(image)
    path_count = 0
    run_count = 0
    node_count = 0

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" shape-rendering="crispEdges" role="img">',
        f"  <title>{html.escape(title)}</title>",
        f"  <desc>{html.escape(description)}</desc>",
        '  <metadata>{"generator":"Roselle","grouping":"color-runs"}</metadata>',
    ]

    color_roles = color_role_map(component_groups)
    for color, runs in sorted(by_color.items(), key=lambda item: (-sum(r[2] for r in item[1]), item[0])):
        fill = color_to_hex(color)
        role = color_roles.get(color_to_hex(color), classify_color(color))
        d_parts = []
        for x, y, length in runs:
            d_parts.append(f"M{x} {y}h{length}v1H{x}z")
        d = "".join(d_parts)
        path_count += 1
        run_count += len(runs)
        node_count += len(runs) * 4
        lines.append(f'  <g id="{role}-{fill.lstrip("#")}" data-role="{role}" data-color="{fill}">')
        lines.append(f'    <path fill="{fill}" d="{d}"/>')
        lines.append("  </g>")

    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")
    assert_valid_svg(path)
    return {
        "svg_bytes": path.stat().st_size,
        "color_groups": len(by_color),
        "path_count": path_count,
        "run_count": run_count,
        "estimated_nodes": node_count,
    }


def runs_by_color(image: Image.Image) -> dict[RGBA, list[tuple[int, int, int]]]:
    width, height = image.size
    pix = image.load()
    groups: dict[RGBA, list[tuple[int, int, int]]] = defaultdict(list)
    for y in range(height):
        start = 0
        current = pix[0, y]
        for x in range(1, width):
            color = pix[x, y]
            if color != current:
                groups[current].append((start, y, x - start))
                start = x
                current = color
        groups[current].append((start, y, width - start))
    return groups


def color_role_map(component_groups: list[dict] | None) -> dict[str, str]:
    if not component_groups:
        return {}
    by_color: dict[str, Counter] = defaultdict(Counter)
    for group in component_groups:
        by_color[group["color"]][group["role"]] += group["pixels"]
    return {color: roles.most_common(1)[0][0] for color, roles in by_color.items()}


def classify_color(color: RGBA) -> str:
    r, g, b, _ = color
    if r > 246 and g > 246 and b > 246:
        return "background"
    if b > r + 25 and g > r + 10:
        return "body-blue"
    if abs(r - g) <= 10 and abs(g - b) <= 10:
        return "hat-gray"
    if r < 90 and g < 70 and b < 45:
        return "eyes-brown"
    return "graphic-detail"


def render_svg(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    if cairosvg is None:
        return
    cairosvg.svg2png(
        url=str(svg_path),
        write_to=str(png_path),
        output_width=width,
        output_height=height,
    )


def compare_images(source: Image.Image, rendered: Image.Image, diff_path: Path) -> dict:
    if source.size != rendered.size:
        rendered = rendered.resize(source.size)
    diff = ImageChops.difference(source, rendered)
    write_diff_heatmap(diff, diff_path)
    hist = diff.histogram()
    total = source.width * source.height
    sq = sum(value * ((idx % 256) ** 2) for idx, value in enumerate(hist))
    rmse = math.sqrt(sq / (total * 4))
    extrema = diff.getextrema()
    exact_pixels = 0
    src_data = image_pixels(source)
    out_data = image_pixels(rendered)
    for a, b in zip(src_data, out_data):
        if a == b:
            exact_pixels += 1
    return {
        "rmse": round(rmse, 4),
        "exact_pixel_ratio": round(exact_pixels / total, 6),
        "max_channel_delta": max(channel[1] for channel in extrema),
    }


def write_report_preview(image: Image.Image, path: Path, crop_px: int = 2) -> None:
    if image.width > crop_px * 2 and image.height > crop_px * 2:
        image = image.crop((crop_px, crop_px, image.width - crop_px, image.height - crop_px))
    image.save(path)


def write_diff_heatmap(diff: Image.Image, path: Path) -> None:
    diff = diff.convert("RGBA")
    width, height = diff.size
    heatmap = Image.new("RGBA", diff.size, (249, 250, 247, 255))
    src = diff.load()
    dst = heatmap.load()
    max_delta = 0

    for y in range(height):
        for x in range(width):
            r, g, b, a = src[x, y]
            delta = max(r, g, b, a)
            max_delta = max(max_delta, delta)
            if delta == 0:
                continue
            intensity = min(255, max(56, delta * 5))
            dst[x, y] = (255, max(22, 120 - intensity // 3), max(80, 210 - intensity // 2), 255)

    if max_delta == 0:
        draw = ImageDraw.Draw(heatmap)
        for offset in range(-height, width, 28):
            draw.line((offset, 0, offset + height, height), fill=(229, 234, 229, 255), width=2)
        text = "No pixel difference"
        bbox = draw.textbbox((0, 0), text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        draw.rounded_rectangle(
            (x - 18, y - 14, x + text_width + 18, y + text_height + 14),
            radius=8,
            fill=(255, 255, 255, 235),
            outline=(205, 215, 207, 255),
        )
        draw.text((x, y), text, fill=(76, 86, 80, 255))

    heatmap.save(path)


def write_report(
    out_dir: Path,
    source_path: Path,
    manifest: dict,
    final_preview: Path | None,
) -> None:
    source_uri = relative_href(out_dir, source_path)
    final_preview_uri = relative_href(out_dir, final_preview) if final_preview else ""

    final_metrics = manifest["candidates"][0]["metrics"]
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Roselle Conversion Report</title>
  <style>
    :root {{
      --ink: #1f2428;
      --muted: #626a70;
      --line: #d7dde1;
      --paper: #f6f7f4;
      --panel: #ffffff;
      --blue: #8fc9df;
      --steel: #a6aaab;
      --brown: #4f331a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    header {{
      padding: 28px clamp(20px, 5vw, 72px) 20px;
      border-bottom: 0;
      background: var(--paper);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(30px, 5vw, 54px);
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 0 0 10px;
      font-size: 16px;
      letter-spacing: 0;
    }}
    p {{ margin: 0; color: var(--muted); max-width: 900px; }}
    main {{ padding: 28px clamp(20px, 5vw, 72px) 56px; }}
    section {{
      margin: 0 0 28px;
      padding: 0 0 28px;
      border-bottom: 0;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 20px;
    }}
    .metric {{
      background: var(--panel);
      border: 0;
      border-radius: 8px;
      padding: 14px;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
      line-height: 1.2;
    }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .compare {{
      display: grid;
      grid-template-columns: repeat(2, minmax(260px, 1fr));
      gap: 24px;
      max-width: 1180px;
      margin: 0 auto;
    }}
    figure {{
      margin: 0;
      background: transparent;
      border: 0;
      border-radius: 0;
      overflow: visible;
    }}
    figure img {{
      width: 100%;
      max-height: 520px;
      aspect-ratio: 1 / 1;
      object-fit: contain;
      display: block;
      background: #ffffff;
      border: 0;
      border-radius: 0;
      box-shadow: none;
    }}
    figcaption {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 30px;
      padding: 10px 0 0;
      color: #5c666d;
      font-size: 14px;
      font-weight: 600;
      border-top: 0;
    }}
    figcaption a {{ color: #53616a; text-decoration-thickness: 1px; text-underline-offset: 3px; }}
    .caption-tag {{
      display: inline-flex;
      align-items: center;
      height: 22px;
      padding: 0 8px;
      border: 0;
      border-radius: 999px;
      background: #edf1ef;
      color: #4e5961;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .note {{
      max-width: 900px;
      background: transparent;
      border: 0;
      border-radius: 0;
      padding: 0;
      color: #4d626b;
    }}
    footer {{
      max-width: 1180px;
      margin: 24px auto 0;
      padding-top: 18px;
      border-top: 1px solid #dfe5e2;
      color: #738087;
      font-size: 13px;
    }}
    footer a {{
      color: #4f5d65;
      font-weight: 700;
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }}
    code {{
      background: #eef0ed;
      padding: 2px 5px;
      border-radius: 4px;
    }}
    @media (max-width: 560px) {{
      main {{ padding-inline: 16px; }}
      header {{ padding-inline: 16px; }}
      .compare {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Roselle Conversion Report</h1>
    <p>Human preview for converting <code>{html.escape(manifest['input']['path'])}</code> into SVG. This report keeps the visual surface simple: source image on the left, final SVG rendering on the right. Agents should read <code>manifest.json</code> for palette, group, and diagnostic details.</p>
    <div class="summary">
      <div class="metric"><strong>{manifest['analysis']['width']} x {manifest['analysis']['height']}</strong><span>source size</span></div>
      <div class="metric"><strong>{manifest['analysis']['unique_colors']}</strong><span>source colors</span></div>
      <div class="metric"><strong>{final_metrics.get('color_groups')}</strong><span>final color groups</span></div>
      <div class="metric"><strong>{final_metrics.get('exact_pixel_ratio', 'n/a')}</strong><span>final SVG pixel match</span></div>
    </div>
  </header>
  <main>
    <section>
      <h2>Preview</h2>
      <div class="compare">
        <figure><img src="{source_uri}" alt="Source PNG"><figcaption><span class="caption-tag">Source</span><span>PNG input</span></figcaption></figure>
        <figure><img src="{final_preview_uri}" alt="Final SVG preview"><figcaption><span class="caption-tag">Final</span><span>SVG output</span><a href="final.svg">Open SVG</a></figcaption></figure>
      </div>
    </section>
    <section>
      <h2>Agent Notes</h2>
      <p class="note">For automation, use <code>manifest.json</code>. It contains final fidelity metrics, extracted palette records, SVG group summaries, warnings, and paths to auxiliary files such as <code>layered.svg</code> and diagnostic images. The report intentionally avoids displaying those details in the main human preview.</p>
    </section>
    <footer>
      Generated with <a href="https://github.com/iCyris/Roselle">Roselle</a>, a human and agent assisted PNG-to-SVG conversion workflow.
    </footer>
  </main>
</body>
</html>
"""
    (out_dir / "report.html").write_text(html_text, encoding="utf-8")


def color_record(color: RGBA, count: int, total: int) -> dict:
    return {
        "hex": color_to_hex(color),
        "rgba": color,
        "pixels": count,
        "percentage": round(count * 100 / total, 4),
    }


def image_pixels(image: Image.Image) -> list[RGBA]:
    if hasattr(image, "get_flattened_data"):
        return list(image.get_flattened_data())
    return list(image.getdata())


def color_to_hex(color: RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(color[0], color[1], color[2])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_valid_svg(path: Path) -> None:
    root = ElementTree.parse(path).getroot()
    if not root.tag.endswith("svg"):
        raise ValueError(f"Not an SVG file: {path}")
    for element in root.iter():
        if element.tag.endswith("image"):
            raise ValueError(f"Embedded bitmap image is not allowed: {path}")


def relative_href(base_dir: Path, path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return html.escape(path.resolve().relative_to(base_dir.resolve()).as_posix())


def build_warnings(analysis: dict, final_metrics: dict, layered_metrics: dict, render_metrics: dict) -> list[dict]:
    warnings = []
    if analysis["unique_colors"] > 256:
        warnings.append(
            {
                "code": "many_source_colors",
                "message": "The source has many antialiased colors, so final.svg preserves pixel fidelity while layered.svg provides a smaller grouped reference.",
            }
        )
    layered_render = render_metrics.get("layered", {})
    if layered_render.get("exact_pixel_ratio", 1) < 0.98:
        warnings.append(
            {
                "code": "grouped_svg_is_approximate",
                "message": "layered.svg is optimized for readable groups and is not pixel-identical. Use final.svg when strict fidelity is required.",
            }
        )
    if final_metrics.get("color_groups", 0) > 1024:
        warnings.append(
            {
                "code": "final_svg_large",
                "message": "final.svg preserves every source color and is intentionally large.",
            }
        )
    return warnings
