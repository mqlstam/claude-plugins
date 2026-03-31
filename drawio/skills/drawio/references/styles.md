# draw.io Style Reference

Style strings are semicolon-separated `key=value` pairs. No spaces around `=` or `;`. Always end with `;`.

## Shape Types

**Core:** rectangle (default), ellipse, rhombus, triangle, hexagon, cloud, cylinder, line, arrow, swimlane, actor

**Extended:** cube, cylinder3, datastore, note, document, folder, card, tape, process, step, callout, parallelogram, trapezoid, singleArrow, doubleArrow, flexArrow, table, tableRow

**UML:** umlActor, umlBoundary, umlEntity, umlControl, umlLifeline, umlFrame, umlState, module, component

**Stencil libraries:** `mxgraph.flowchart.*`, `mxgraph.aws4.*`, `mxgraph.azure.*`, `mxgraph.gcp2.*`, `mxgraph.kubernetes.*`, `mxgraph.uml.*`, `mxgraph.er.*`, `mxgraph.mockup.*`

## Fill & Stroke

| Property | Values | Default |
|----------|--------|---------|
| `fillColor` | #RRGGBB, none, default | default |
| `gradientColor` | #RRGGBB, none | none |
| `strokeColor` | #RRGGBB, none, default | default |
| `strokeWidth` | number | 1 |
| `dashed` | 0, 1 | 0 |
| `opacity` | 0-100 | 100 |
| `shadow` | 0, 1 | 0 |
| `rounded` | 0, 1 | 0 |
| `arcSize` | number (0-50%) | - |
| `glass` | 0, 1 | 0 |
| `sketch` | 0, 1 | 0 |

## Text & Labels

| Property | Values | Default |
|----------|--------|---------|
| `html` | 0, 1 | 1 |
| `whiteSpace` | wrap, nowrap | - |
| `fontSize` | number | 12 |
| `fontFamily` | string | Helvetica |
| `fontColor` | #RRGGBB, default | default |
| `fontStyle` | bitmask: 1=bold, 2=italic, 4=underline | 0 |
| `align` | left, center, right | center |
| `verticalAlign` | top, middle, bottom | middle |
| `spacing` | number | 2 |
| `labelBackgroundColor` | #RRGGBB, none | - |

## Edge Properties

| Property | Values | Default |
|----------|--------|---------|
| `edgeStyle` | orthogonalEdgeStyle, elbowEdgeStyle, entityRelationEdgeStyle, segmentEdgeStyle | - |
| `curved` | 0, 1 | 0 |
| `rounded` | 0, 1 | 1 |
| `jettySize` | auto, number | auto |
| `jumpStyle` | arc, gap, sharp | - |

## Arrow Markers

| Property | Values |
|----------|--------|
| `startArrow` / `endArrow` | none, classic, classicThin, block, open, oval, diamond, diamondThin, box, halfCircle, circle, dash |
| `startFill` / `endFill` | 0, 1 |
| `startSize` / `endSize` | number |

## Connection Points

| Property | Values | Use |
|----------|--------|-----|
| `exitX` / `exitY` | 0.0-1.0 | Where edge leaves source |
| `entryX` / `entryY` | 0.0-1.0 | Where edge enters target |

## Container Properties

| Property | Values | Default |
|----------|--------|---------|
| `container` | 0, 1 | 0 |
| `collapsible` | 0, 1 | 1 |
| `startSize` | number | 23 |
| `swimlaneFillColor` | #RRGGBB | - |
| `pointerEvents` | 0, 1 | 1 |

## Geometry & Behavior

| Property | Values | Default |
|----------|--------|---------|
| `rotation` | degrees | 0 |
| `flipH` / `flipV` | 0, 1 | 0 |
| `movable` / `resizable` / `editable` | 0, 1 | 1 |
| `connectable` | 0, 1 | 1 |
| `autosize` | 0, 1 | 0 |

## Perimeter Types

Use matching perimeter for non-rectangular shapes:
- `rectanglePerimeter`, `ellipsePerimeter`, `rhombusPerimeter`, `trianglePerimeter`, `hexagonPerimeter2`, `parallelogramPerimeter`, `trapezoidPerimeter`

## Standard Color Palette

| Light fill | Matching stroke | Use |
|------------|----------------|-----|
| #DAE8FC | #6C8EBF | Blue |
| #D5E8D4 | #82B366 | Green |
| #FFF2CC | #D6B656 | Yellow |
| #F8CECC | #B85450 | Red |
| #E1D5E7 | #9673A6 | Purple |
| #FFE6CC | #D79B00 | Orange |
| #F5F5F5 | #666666 | Gray |

## Dark Mode

Set `adaptiveColors="auto"` on `<mxGraphModel>`. Colors auto-invert for dark mode. Use `light-dark(lightColor,darkColor)` for explicit control.
