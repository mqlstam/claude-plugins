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

## Personal Design System

**Always apply this design system unless the user explicitly asks for a different style.**

This is the default visual language for all diagrams. Sharp corners where noted, no emojis, no gradients, no shadows, no glass effects.

### Palette

One hue — three intensities only.

| Token | Value | Use |
|-------|-------|-----|
| Bright | `#00BACB` | Primary borders, accent fills, accent edges |
| Mid | `#00909E` | Secondary borders, inner container borders |
| Deep | `#00363D` | Dark container fills, accent text on light |

**Dark theme surfaces (default):**

| Token | Value | Use |
|-------|-------|-----|
| BG | `#141413` | Page background |
| Surface 1 | `#1E1E1D` | Cards, primary nodes |
| Surface 2 | `#252524` | Elevated cards |
| Surface 3 | `#323230` | Edge stroke, tertiary borders |

**Dark theme text:**

| Token | Value | Use |
|-------|-------|-----|
| Primary | `#F0F0EE` | Card titles, bold labels |
| Secondary | `#888886` | Body copy, descriptions |
| Muted | `#555558` | Captions, deemphasized |

**Light theme surfaces:**

| Token | Value | Use |
|-------|-------|-----|
| BG | `#F7F7F5` | Page background |
| Surface 1 | `#FFFFFF` | Cards, primary nodes |
| Surface 2 | `#EFEFED` | Muted areas |
| Surface 3 | `#E0E0DE` | Borders, edge stroke |

**Light theme text:**

| Token | Value | Use |
|-------|-------|-----|
| Primary | `#1A1A18` | Card titles, bold labels |
| Secondary | `#666664` | Body copy, descriptions |
| Muted | `#ABABAA` | Captions, deemphasized |

---

### Node Hierarchy

Border intensity = importance. Fill color is always the appropriate surface — only the border varies by tier.

**Dark theme nodes:**

```
Primary   fillColor=#1E1E1D; strokeColor=#00BACB; fontColor=#F0F0EE
Secondary fillColor=#1E1E1D; strokeColor=#00909E; fontColor=#F0F0EE
Tertiary  fillColor=#1E1E1D; strokeColor=#323230; fontColor=#888886
Accent    fillColor=#00BACB; strokeColor=#00909E; fontColor=#FFFFFF   ← entry points, highlights
External  fillColor=#1E1E1D; strokeColor=#323230; fontColor=#888886; dashed=1; dashPattern=8 4
```

**Light theme nodes:**

```
Primary   fillColor=#FFFFFF; strokeColor=#00BACB; fontColor=#1A1A18
Secondary fillColor=#FFFFFF; strokeColor=#00909E; fontColor=#1A1A18
Tertiary  fillColor=#FFFFFF; strokeColor=#E0E0DE; fontColor=#666664
Accent    fillColor=#00BACB; strokeColor=#00909E; fontColor=#FFFFFF   ← entry points, highlights
External  fillColor=#FFFFFF; strokeColor=#E0E0DE; fontColor=#666664; dashed=1; dashPattern=8 4
```

**Style template for cards:**
```
rounded=1;whiteSpace=wrap;html=1;arcSize=12;
align=left;spacingLeft=16;spacingRight=16;spacingTop=12;spacingBottom=12;
fontFamily=Helvetica Neue;fontSize=12;
```

**Card label HTML pattern:**
```xml
<font style="font-size:14px"><b>Title</b></font><br>
<font color="#888886" style="font-size:12px">Body description</font>
```
(use `#666664` for body on light theme)

---

### Containers

**Neutral boundary (dashed outline):**
```
Dark:  fillColor=none; strokeColor=#323230; fontColor=#555558; dashed=1; dashPattern=8 4; arcSize=8
Light: fillColor=none; strokeColor=#E0E0DE; fontColor=#ABABAA; dashed=1; dashPattern=8 4; arcSize=8
```

**Accent group (filled, shows logical grouping):**
```
Dark:  fillColor=#00363D; strokeColor=#00909E; fontColor=#7DE4EE; arcSize=8
Light: fillColor=#E6F9FB; strokeColor=#00BACB; fontColor=#00363D; arcSize=8
```
Inner cards inside accent group use Secondary style (`strokeColor=#00909E`).

**Container style template:**
```
rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;spacingLeft=16;spacingTop=12;
fontFamily=Helvetica Neue;fontSize=15;align=left;
```

---

### Edges

All edges: `endArrow=open;endFill=0;curved=1;rounded=1;`

| Type | Style |
|------|-------|
| Standard | `strokeColor=#323230` (dark) / `#E0E0DE` (light); no label or muted label |
| Important / accent | `strokeColor=#00BACB` |
| Async / durable | add `dashed=1;dashPattern=8 4` |

Edge label style: `fontColor=#555558` (dark) / `#ABABAA` (light); `fontSize=11`

---

### Special Shapes

**Database / data store cylinder:**
```
shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=12;
```

**Status pill (fully rounded badge):**
```
arcSize=50; fillColor=#00BACB; strokeColor=none; fontColor=#FFFFFF
```
Pending: `fillColor=#00909E`. Inactive: `fillColor=#EFEFED;strokeColor=#E0E0DE;fontColor=#ABABAA`.

**Callout / annotation (no fill, dashed):**
```
fillColor=none; strokeColor=#323230; dashed=1; dashPattern=4 4
```
(use `#E0E0DE` stroke on light theme)

---

### Typography

- Font: `fontFamily=Helvetica Neue` on all cells
- No emojis. No decorative icons.
- Section headers: standalone `text` cells, `fontSize=22;fontStyle=0`
- Subtitles / captions: `fontSize=13` or `fontSize=11`, muted color
- Card title: `fontSize=14;fontStyle=1` (bold) inside HTML label
- Card body: `fontSize=12;fontStyle=0`, secondary color

---

### Grid & Spacing

- `gridSize=8` (8pt grid)
- `arcSize=12` for cards, `arcSize=8` for containers
- Horizontal gap between nodes: 200px
- Vertical gap: 120px minimum
- Inner padding: `spacingLeft=16;spacingRight=16;spacingTop=12;spacingBottom=12`

---

### Theme Selection

- **Default: dark theme** (`background=#141413`)
- Use light theme only if user says "light", "white", or "print"

Set page background on the `<mxGraphModel>` tag:
```xml
<!-- Dark -->
<mxGraphModel background="#141413" ...>
<!-- Light -->
<mxGraphModel background="#F7F7F5" ...>
```

---

## Output format

Check the user's request for a format preference:

- `/drawio create a flowchart` → `flowchart.drawio`
- `/drawio png flowchart for login` → `login-flow.drawio.png`
- `/drawio svg: ER diagram` → `er-diagram.drawio.svg`
- `/drawio pdf architecture overview` → `architecture-overview.drawio.pdf`

If no format is mentioned, write the `.drawio` file and open it.

## XML structure

```xml
<mxGraphModel background="#141413" dx="1422" dy="762" grid="1" gridSize="8" ...>
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
- Space nodes generously: 200px horizontal / 120px vertical gaps
- Align to grid (multiples of 8)
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
