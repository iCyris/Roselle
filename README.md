# Roselle

<p align="center">
  <img src="assets/fleur-roselle-logo.png" alt="Roselle logo" width="180">
</p>

<h1 align="center">Roselle</h1>

<p align="center">
  <strong>Turn raster graphics into precise, editable SVG assets.</strong>
</p>

Roselle converts PNG, JPG, and WebP graphics into editable SVG assets. It produces a pixel-faithful `final.svg`, a color-grouped `layered.svg` for editing and animation, and a review bundle with previews, metrics, palette data, and a machine-readable manifest.

Use it for icons, logos, sketches, diagrams, line art, UI assets, and ornamental graphics. Everything runs locally, and both people and AI agents can compare the outputs and choose between exact delivery and an editable reference.

## What Roselle Does

- Converts raster images into SVG files without embedding the original bitmap.
- Generates `final.svg` as a pixel-fidelity vector delivery for strict visual matching.
- Generates `layered.svg` as a smaller color-grouped reference for editing and animation prep.
- Extracts palette information and role-oriented SVG groups.
- Produces a static HTML report with a focused source-to-final preview.
- Writes a machine-readable manifest for agent workflows.

## Quick Start

Roselle is currently a Python package in early implementation form.

```bash
PYTHONPATH=src python3 -m roselle.cli vectorize test/foo.png --out-dir out/foo --json
```

The command writes a review bundle:

```text
out/foo/
  final.svg
  layered.svg
  manifest.json
  analysis.json
  report.html
  renders/
    final.png
    layered.png
  diffs/
    final.png
    layered.png
```

## Output Model

`final.svg` is the faithful delivery file. It preserves every source color as grouped vector paths. This can be large, but it gives the workflow a reliable baseline when the requirement is visual fidelity.

`layered.svg` is the editable reference. It uses an extracted palette and role-oriented groups such as background, body color, hat color, eye color, and detail colors. This file is useful when the next step is animation, Rive preparation, or manual SVG cleanup.

`report.html` is the human review surface. It intentionally shows only the source image and the final SVG preview, so users can quickly answer the basic question: did the conversion preserve the image? It links to image files instead of embedding them as base64 text, so it stays readable and does not become an accidental AI context sink.

`manifest.json` is the agent contract. It records input metadata, output paths, palette records, group summaries, candidate metrics, warnings, and auxiliary files such as `layered.svg` and diagnostic images.

## Using Roselle With an AI Agent

The recommended agent workflow is:

```text
1. Ask the agent to run Roselle on a local image path.
2. Have the agent read manifest.json and analysis.json.
3. Have the agent inspect small previews only when visual judgment is needed.
4. Avoid pasting final.svg, layered.svg, report.html, or base64 image data into the chat.
5. Use the file paths from manifest.json when handing SVG assets to later tools.
```

Example prompt:

```text
Run:
PYTHONPATH=src python3 -m roselle.cli vectorize test/foo.png --out-dir out/foo --json

Then read out/foo/manifest.json and summarize:
- final.svg fidelity metrics
- layered.svg color groups
- warnings
- which file should be used for exact delivery vs animation prep
Do not paste the SVG path data or report HTML into the chat.
```

For agents, `manifest.json` is the primary artifact. `report.html` is for human preview. The SVG files can be large because they contain real vector paths, so agents should reference them by path instead of reading them as text unless they need to inspect SVG internals directly.

## Current Test Case

The fixture at `test/foo.png` is a 1254 x 1254 RGB image with soft edges, gradients, and thousands of antialiased colors.

Current generated metrics:

```text
final.svg
  exact pixel ratio: 1.0
  RMSE: 0.0
  color groups: 6562
  embedded bitmap images: 0

layered.svg
  color groups: 20
  purpose: readable grouped reference
  preserves major body, hat, eye, background, and detail color families
```

This split is intentional: one SVG proves fidelity, the other helps people and agents reason about editable groups.

## Architecture

```text
Input image
  -> image analysis
  -> palette extraction
  -> pixel-fidelity SVG generation
  -> layered SVG generation
  -> preview and diff generation
  -> manifest and report generation
```

The implementation is local-first and rules-based. AI planning can be added later as an optional layer, but the conversion path should stay reproducible without a model call.

## Philosophy

Roselle treats every conversion as a restoration task:

1. Preserve what matters.
2. Remove what does not.
3. Rebuild the image as a controllable vector asset.
4. Verify that the final SVG remains faithful, editable, and practical.

The result is a restored design asset ready for design systems, websites, apps, documentation, and further creative work.

## License

MIT. See [LICENSE](LICENSE) for details.
