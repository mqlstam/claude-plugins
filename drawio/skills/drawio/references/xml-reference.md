# draw.io XML Reference

Detailed reference for XML patterns, edge routing, and containers.

## Basic Shapes

**Rounded rectangle:**
```xml
<mxCell id="2" value="Label" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
</mxCell>
```

**Diamond (decision):**
```xml
<mxCell id="3" value="Condition?" style="rhombus;whiteSpace=wrap;" vertex="1" parent="1">
  <mxGeometry x="100" y="200" width="120" height="80" as="geometry"/>
</mxCell>
```

**Database cylinder:**
```xml
<mxCell id="4" value="Database" style="shape=cylinder3;whiteSpace=wrap;boundedLbl=1;backgroundOutline=1;size=15;" vertex="1" parent="1">
  <mxGeometry x="100" y="300" width="120" height="80" as="geometry"/>
</mxCell>
```

## Edge Routing

**CRITICAL: Every edge MUST have a child `<mxGeometry>` element.** Self-closing edge cells are invalid.

**Basic edge:**
```xml
<mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="2" target="3">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

**Labeled edge:**
```xml
<mxCell id="e2" value="Yes" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="3" target="4">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

**Edge with explicit waypoints** (use when edges overlap):
```xml
<mxCell id="e3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="a" target="b">
  <mxGeometry relative="1" as="geometry">
    <Array as="points">
      <mxPoint x="300" y="150"/>
      <mxPoint x="300" y="250"/>
    </Array>
  </mxGeometry>
</mxCell>
```

**Edge tips:**
- `edgeStyle=orthogonalEdgeStyle` for right-angle connectors (most common)
- `rounded=1` on edges for cleaner bends
- `jettySize=auto` for better port spacing
- Space nodes 200px horizontal / 120px vertical minimum
- Leave 20px+ of straight segment before target for arrowheads
- Use `exitX`/`exitY` and `entryX`/`entryY` (0.0-1.0) to control connection sides
- Do NOT wrap edge labels in HTML to reduce font size — default 11px is already smaller than vertex labels

## Containers and Groups

Use parent-child containment (`parent="containerId"`) — do NOT just stack shapes.

### Container types

| Type | Style | When to use |
|------|-------|-------------|
| Group (invisible) | `group;` | No visible border, no connections to container |
| Swimlane (titled) | `swimlane;startSize=30;` | Visible header, container may have connections |
| Custom container | Any shape + `container=1;pointerEvents=0;` | Shaped container without connections |

### Key rules
- Always add `pointerEvents=0;` unless container itself needs connections
- Children use `parent="containerId"` with **relative coordinates**

### Swimlane example:
```xml
<mxCell id="svc1" value="User Service" style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="300" height="200" as="geometry"/>
</mxCell>
<mxCell id="api1" value="REST API" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="svc1">
  <mxGeometry x="20" y="40" width="120" height="60" as="geometry"/>
</mxCell>
<mxCell id="db1" value="Database" style="shape=cylinder3;whiteSpace=wrap;" vertex="1" parent="svc1">
  <mxGeometry x="160" y="40" width="120" height="60" as="geometry"/>
</mxCell>
```

### Group example:
```xml
<mxCell id="grp1" value="" style="group;" vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="300" height="200" as="geometry"/>
</mxCell>
<mxCell id="c1" value="Component A" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="grp1">
  <mxGeometry x="10" y="10" width="120" height="60" as="geometry"/>
</mxCell>
```

## Custom Metadata

Wrap `mxCell` in `object` for custom key-value metadata:
```xml
<object id="srv1" label="Web Server" ip="10.0.1.10" environment="production">
  <mxCell style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="100" y="100" width="140" height="70" as="geometry"/>
  </mxCell>
</object>
```

## File-Level Variables

```xml
<mxfile vars='{"project":"Atlas","version":"2.1"}'>
```
Reference with `%project%` in labels. Requires `placeholders="1"` on the UserObject.

## XML Well-Formedness

- Never use `--` inside XML comments
- Escape: `&amp;`, `&lt;`, `&gt;`, `&quot;`
- All IDs must be unique
- Always include cells `id="0"` and `id="1" parent="0"`
