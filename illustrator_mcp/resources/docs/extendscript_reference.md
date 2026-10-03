# Illustrator ExtendScript Quick Reference

## Coordinate System
- The DOM is Y-up: y grows upward. `artboardRect` is `[left, top, right, bottom]` with top > bottom.
- An artboard does not have to start at y = 0: a new 600x400 document has `[0, 400, 600, 0]`.
- Visual position (x, y) measured from an artboard's top-left:
  `var r = doc.artboards[i].artboardRect; item.position = [r[0] + x, r[1] - y];`
- Typed tools (`illustrator_inspect`, `illustrator_artboards`, ...) report canvas-global Y-down
  bounds: `[left, -top_dom, right, -bottom_dom]`. Convert back with `y_dom = -y`.
- `illustrator_execute_task` element ops take x, y relative to the active artboard's top-left, Y-down.
- Units: Points (1 pt = 1/72 inch)

## Common Patterns

### Access Document
```javascript
var doc = app.activeDocument;
var layer = doc.activeLayer;
```

### Create Shapes
```javascript
// Rectangle: rectangle(top, left, width, height)
doc.pathItems.rectangle(-100, 50, 200, 100);

// Ellipse: ellipse(top, left, width, height)
doc.pathItems.ellipse(-100, 50, 100, 100);

// Rounded Rectangle: roundedRectangle(top, left, width, height, hRadius, vRadius)
doc.pathItems.roundedRectangle(-100, 50, 200, 100, 10, 10);

// Polygon: polygon(centerX, centerY, radius, sides)
doc.pathItems.polygon(100, -200, 50, 6);

// Star: star(centerX, centerY, outerR, innerR, points)
doc.pathItems.star(100, -200, 50, 25, 5);

// Line (path with 2 points)
var line = doc.pathItems.add();
line.setEntirePath([[x1, -y1], [x2, -y2]]);
```

### Colors
```javascript
// RGB Color
var c = new RGBColor();
c.red = 255; c.green = 0; c.blue = 0;

// CMYK Color
var cmyk = new CMYKColor();
cmyk.cyan = 100; cmyk.magenta = 0; cmyk.yellow = 0; cmyk.black = 0;

// Apply to shape
shape.fillColor = c;
shape.strokeColor = c;
shape.strokeWidth = 2;

// No fill/stroke
shape.filled = false;
shape.stroked = false;
```

### Gradients
```javascript
// Create linear gradient
var gradient = doc.gradients.add();
gradient.name = "MyGradient";
gradient.type = GradientType.LINEAR;

// Set color stops
var stop1 = gradient.gradientStops[0];
var blue = new RGBColor(); blue.red = 0; blue.green = 100; blue.blue = 255;
stop1.color = blue;
stop1.rampPoint = 0;

var stop2 = gradient.gradientStops[1];
var purple = new RGBColor(); purple.red = 128; purple.green = 0; purple.blue = 255;
stop2.color = purple;
stop2.rampPoint = 100;

// Apply to shape
var gradColor = new GradientColor();
gradColor.gradient = gradient;
gradColor.angle = 45;  // degrees
shape.fillColor = gradColor;

// Radial gradient
gradient.type = GradientType.RADIAL;
```

### Text
```javascript
var tf = doc.textFrames.add();
tf.contents = "Hello World";
var r = doc.artboards[0].artboardRect;
tf.position = [r[0] + x, r[1] - y];  // visual (x, y) from the artboard top-left

// Style text
tf.textRange.characterAttributes.size = 12;
tf.textRange.characterAttributes.fillColor = c;

// Set font
tf.textRange.characterAttributes.textFont = app.textFonts.getByName("Arial-BoldMT");
```

### Layers
```javascript
// Create layer
var newLayer = doc.layers.add();
newLayer.name = "My Layer";

// Access layer
var layer = doc.layers.getByName("Layer 1");

// Move item to layer
item.move(layer, ElementPlacement.PLACEATBEGINNING);
```

### Selection
```javascript
// Get selection
var sel = doc.selection;
if (sel.length > 0) {
    sel[0].remove();  // Delete first selected
}

// Select by name
var item = doc.pageItems.getByName("MyShape");
item.selected = true;

// Deselect all
doc.selection = null;
```

### Groups
```javascript
// Create group
var group = doc.groupItems.add();
group.name = "My Group";

// Add items to group via move
item.move(group, ElementPlacement.PLACEATBEGINNING);

// Ungroup (move items out)
for (var i = group.pageItems.length - 1; i >= 0; i--) {
    group.pageItems[i].move(doc.activeLayer, ElementPlacement.PLACEATEND);
}
```

### Transform
```javascript
// Move
item.translate(deltaX, -deltaY);

// Scale (percentage)
item.resize(120, 120);  // 120% scale

// Rotate (degrees)
item.rotate(45);

// Reflect
item.reflect(true, false);  // horizontal, vertical
```

### Pathfinder Operations
```javascript
// Unite (merge shapes)
app.executeMenuCommand('Live Pathfinder Add');

// Subtract (cut out)
app.executeMenuCommand('Live Pathfinder Subtract');

// Intersect
app.executeMenuCommand('Live Pathfinder Intersect');

// Exclude (XOR)
app.executeMenuCommand('Live Pathfinder Exclude');

// Expand after pathfinder
app.executeMenuCommand('expandStyle');
```

### Clipping Masks
```javascript
// Create clipping mask (top item clips the rest)
// First, select the items to mask
doc.selection = [clipPath, itemToClip];

// Apply clipping mask
app.executeMenuCommand('makeMask');

// Release clipping mask
app.executeMenuCommand('releaseMask');
```

### Symbols
```javascript
// Create symbol from selection
var sel = doc.selection[0];
var symbol = doc.symbols.add(sel, SymbolRegistrationPoint.SYMBOLCENTERPOINT);
symbol.name = "MySymbol";

// Place symbol instance
var instance = doc.symbolItems.add(symbol);
instance.left = 100;
instance.top = -100;
```

### Compound Paths
```javascript
// Create compound path from selection
app.executeMenuCommand('compoundPath');

// Release compound path
app.executeMenuCommand('noCompoundPath');
```

## Z-Order (Stacking Order)

Illustrator uses **index-based z-order**: index 0 = topmost (drawn last), highest index = bottommost (drawn first).

### ElementPlacement Constants
| Constant | Visual Effect | Mnemonic |
|----------|--------------|----------|
| `PLACEBEFORE` | **In front of** reference (lower index) | "before" = closer to viewer |
| `PLACEAFTER` | **Behind** reference (higher index) | "after" = further from viewer |
| `PLACEATBEGINNING` | **Topmost** in container (index 0) | front of stack |
| `PLACEATEND` | **Bottommost** in container (last index) | back of stack |

### Common Patterns
```javascript
// Move item to front of layer (visually on top of everything)
item.move(layer, ElementPlacement.PLACEATBEGINNING);

// Move item to back of layer (visually behind everything)
item.move(layer, ElementPlacement.PLACEATEND);

// Stack A in front of B (A will visually cover B)
itemA.move(itemB, ElementPlacement.PLACEBEFORE);

// Stack A behind B (B will visually cover A)
itemA.move(itemB, ElementPlacement.PLACEAFTER);
```

### Card Recipe (background + content)
When building layered structures (cards with backgrounds), create content **first**, then background **last**, and move the background behind everything:
```javascript
// 1. Create content items first (they get low indices = on top)
var title = layer.textFrames.add();
var dot = layer.pathItems.ellipse(...);

// 2. Create background card LAST
var cardBg = layer.pathItems.rectangle(...);

// 3. cardBg is already at the front (lowest index) — move it behind content
cardBg.move(layer, ElementPlacement.PLACEATEND);

// OR: create bg first, then move each content item in front of it
var cardBg = layer.pathItems.rectangle(...);
title.move(cardBg, ElementPlacement.PLACEBEFORE);  // title in front of bg
```

⚠️ **Common mistake**: Using `PLACEATEND` thinking it means "last created" — it actually means **bottommost z-order** (visually behind everything). Similarly, `PLACEBEFORE` means **visually in front of** the reference item, not "before" in creation order.

## Common Mistakes to Avoid
- Using positive Y for downward (should be negative)
- Using ctx.rect() instead of pathItems.rectangle()
- Forgetting to set filled/stroked properties
- Placing at `[x, -y]` without the artboard's left/top offset (see Coordinate System)
- Forgetting to expand live effects before export
- **Exceeding ~8000 points in setEntirePath()** — Illustrator crashes with 'Illegal Argument'. Use the `generative` library's `decimatePoints()` to auto-clamp.

## Verified Traps (Illustrator 30.8.1)

Each of these was observed live; none raises an error by itself.

Transport
- Illustrator's `JSON` has `stringify` but no `parse`, and `stringify` escapes only `"` and `\n`.
  The repository's host.jsx now installs its own ES3 stringify/parse codec. Return plain data
  or an envelope through JSON.stringify; quotes, backslashes and controls survive.
  Older installed hosts still have this defect; typed tools retain their dm1: wire encoding
  for compatibility. Reload the updated panel and host together.

Identity
- `PageItem.uuid` is a per-document counter, renumbered in document order whenever a file is
  opened; after an edit plus reopen the same uuid can name a different object. Two open documents
  can hold the same uuid. `duplicate()` gets a new one. Layers and artboards have none.
- `doc.getPageItemFromUuid()` ignores `doc` and resolves in `app.activeDocument`, throws on an
  unknown uuid, and returns a `GroupItem`-typed object for a `CompoundPathItem`.
- `@mcp:id` in `item.note` survives save, close and reopen; use it across sessions.
- Inspection adds `document.session_id`. Pass `document_session_id` to typed edits or native
  UUID targets to refuse a reopened/different document even when names and UUIDs collide.

Text
- `textFrame.textRanges` has one range per character, not per style run; rebuild runs by comparing
  neighbours. In the continuation frame of a threaded story it is indexed by story position
  (lower indices throw "The specified text range is invalid"); read through `story.textRanges`.
- `story.characters[i]` with `.length = n` addresses n characters; assigning `.contents` gives the
  new text the attributes of the first character.
- `app.textFonts.getByName()` does not throw for a missing font: Illustrator registers a
  placeholder. A run in a missing font carries a subset-prefixed family such as `XPUYQY+Name`.
- Composed `lines` cover only the visible text; anything after the last line is overset.
  The last frame of a thread reports itself as `nextFrame`; `nextFrame`/`previousFrame` throw for
  point and path text.
- `createOutline()` returns a `GroupItem`, drops the frame's name and note, and the frame's uuid is
  gone; copy name and note onto the group yourself.
- In a CMYK document an assigned RGB color is converted; read it back before comparing.

Effects, swatches, files
- `applyEffect()` accepts an unknown effect name silently; malformed XML throws. Effects cannot be
  read back from script. Prefer `illustrator_effects`.
- `swatch.parent` is always the document (group membership only via `getAllSwatches()`); duplicate
  swatch names are accepted; removing `[None]` or `[Registration]` is a silent no-op.
- `app.open` refuses `.ase`. Opening an `.ai` swatch library with stale links shows a modal dialog
  unless `app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS`.
- `PlacedItem.file` throws "There is no file associated with this item" for a missing link.

## Custom Paths and Polylines
```javascript
// Multi-point path (up to ~8000 points safely)
var path = doc.pathItems.add();
var pts = [[0, 0], [50, -100], [100, 0], [150, -50]];
path.setEntirePath(pts);
path.closed = false;   // Open polyline (default: true for closed)
path.stroked = true;
path.filled = false;
```

## Standard Libraries (use via `includes` parameter)
| Library | Key Functions |
|---------|-------------|
| `geometry` | `rectXY()`, `ellipseXY()`, `lineXY()`, `makeRGBColor()` |
| `layout` | `arrangeInGrid()`, `distributeHorizontal()`, `alignCenter()` |
| `validate` | `countItemsOnArtboard()`, `isItemCenterOnArtboard()` |
| `generative` | `seededRandom()`, `fbm()`, `marchingSquares()`, `chaikinSmooth()` |
| `selection` | `getOrderedSelection()` |
