---
name: project-rendering
description: "Mandatory knowledge and workflow when changing rendering output, the renderer block library, or alarm tile and dial visuals. Use before committing visual changes or claiming rendering parity."
---
# Project Rendering

## Purpose

Prevents committing visual changes the user has not seen, and keeps the renderer block library and alarm visuals consistent.

## Scope

Use this skill when:

- changing key tile or LCD dial rendering in `src/megingjord/render.py`;
- changing the alarm tile or dial visuals in `src/megingjord/ha.py`;
- changing icon handling in `src/megingjord/icon.py`;
- claiming rendering parity or visual correctness.

Do not use this skill for:

- the check suite or live-testing workflow (see `project-testing`);
- non-visual logic changes.

## Mandatory Knowledge

- The renderer block library in `render.py`: `StateTile`, `TransitionTile` (keys), `StateDial`, `SelectionDial`, `ValueDial`, `draw_time` (LCD), plus primitives `draw_icon`, `draw_text`, `wrap_text`.
- Key tiles render in PIL with the Ubuntu font (unified from the earlier SVG composition with Cairo "sans").
- The transition tile shows the primary icon lower left and the secondary icon upper right.
- The dial layout is shared via `_draw_dial`: icon, title, bar label, optional meter bar. The state dial and value dial delegate to it.
- Alarm semantics in `ha.py`: `ALARM_*` constants; state colors `icon-ok` (armed), `icon-warning` (pending/arming), `icon-alert` (triggered), `icon-active` (disarmed), inactive for transitioning.
- `DIAL_TILE_SIZE` is 140x100; the LCD is 800x100.
- Icons: `get_icon` is `alru_cache`d; `svg_icon`/`svg_to_image` live in `icon.py`.

## Best Practices

- Render preview PNGs to `./tmp` (e.g. `./tmp/alarm-previews/`) for the user to inspect before committing visual changes; the user found this helpful.
- Use the `colors` override on the dial and tile blocks for state colors instead of hardcoding.
- Keep the shared dial layout in `_draw_dial`; do not duplicate it in new dial blocks.
- Use theme colors via `get_color` rather than literal hex values.

## Bad Practices

- Committing visual changes without a preview or user inspection.
- Duplicating the dial layout instead of extending `_draw_dial`.
- Hardcoding colors that should come from the theme.

## Workflow Checklist

1. For visual changes, render preview images to `/tmp` and have the user inspect them.
2. Apply the change using the existing blocks and `colors` overrides.
3. Run the check suite (see `project-testing`).
4. Commit only after the user has seen and approved the visuals.

## Validation Checklist

Before claiming done:

- Preview images rendered and inspected by the user for visual changes.
- Check suite passes.
- Same-failure scan: check other blocks for the same pattern (e.g. title overflow, color handling).
- Sensitive data gate: no raw tokens or personal data in durable artifacts.

## Evidence

- `src/megingjord/render.py`: the block library and shared dial layout.
- `src/megingjord/ha.py`: alarm constants and state colors.

## Update Rules

Update this skill when:

- a new block or rendering convention is added;
- the user corrects the preview or visual workflow;
- a rendering failure mode is discovered.
