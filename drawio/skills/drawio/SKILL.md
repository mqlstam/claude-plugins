---
name: drawio
description: >-
  Generate draw.io diagrams as native .drawio files. Use when the user asks to
  create, generate, draw, or design a diagram, flowchart, architecture diagram,
  ER diagram, sequence diagram, class diagram, or mentions draw.io/.drawio files.
---

# Draw.io Diagram Skill

Generate draw.io diagrams as native `.drawio` files. Optionally export to PNG, SVG, or PDF with embedded XML (so the exported file remains editable in draw.io).

## How to create a diagram

1. Generate draw.io XML in mxGraphModel format
2. Write the XML to a `.drawio` file using the Write tool
3. If the user requested an export format, export using the draw.io CLI (see below), then delete the source `.drawio` file
4. Open the result with `open <file>` (macOS)

For style properties, shapes, and colors: read [references/styles.md](references/styles.md).
For XML patterns, edge routing, and containers: read [references/xml-reference.md](references/xml-reference.md).

## Output format

Check the user's request for a format preference:

- `/drawio create a flowchart` → `flowchart.drawio`
- `/drawio png flowchart for login` → `login-flow.drawio.png`
- `/drawio svg: ER diagram` → `er-diagram.drawio.svg`
- `/drawio pdf architecture overview` → `architecture-overview.drawio.pdf`

If no format is mentioned, write the `.drawio` file and open it.

## XML structure

```xml
<mxGraphModel adaptiveColors="auto">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <!-- Diagram cells go here with parent="1" -->
  </root>
</mxGraphModel>
```

**Critical rules:**
- Cell `id="0"` (root) and `id="1"` (default layer) are always required
- All IDs must be unique
- Every edge MUST have `<mxGeometry relative="1" as="geometry" />` as a child — self-closing edge cells are invalid
- Use `edgeStyle=orthogonalEdgeStyle` for right-angle connectors
- Space nodes generously: 200px horizontal / 120px vertical gaps
- Align to grid (multiples of 10)
- Never use double hyphens (`--`) inside XML comments

## draw.io CLI (export)

```bash
/Applications/draw.io.app/Contents/MacOS/draw.io -x -f <format> -e -b 10 -o <output> <input.drawio>
```

Key flags: `-x` export, `-f` format (png/svg/pdf), `-e` embed diagram XML, `-b` border, `-o` output path.

If CLI not found, keep the `.drawio` file and tell the user to install draw.io Desktop or open the file manually.

After successful export, delete the intermediate `.drawio` file — the exported file contains the full diagram.

## File naming

- Descriptive, lowercase, hyphens: `login-flow`, `database-schema`
- Export uses double extension: `name.drawio.png`, `name.drawio.svg`, `name.drawio.pdf`
