"""
test_overlay_legibility.py — annotation labels must survive the whole render.

The annotated preview is how a multimodal client grounds "[N]" back to a
PageItem (illustrator_ground_object). A label that is veiled, cropped, or
painted over is not a cosmetic problem: the grounding handoff silently loses
that item.

Two defects this pins down, both found by rendering the 59-item dashboard
fixture and looking at the result:

1. composite_overlay drew each annotation in one pass — box, then label. Every
   later annotation painted its outline and its translucent fill over the
   labels already on the canvas, so a large item late in the list (a chart
   background, a panel) veiled every number sitting on top of it. On the
   fixture only about 15 of 57 labels stayed legible.

2. _place_pill fell back to an out-of-bounds candidate when no position fit,
   which crops the pill against the canvas edge.
"""

from io import BytesIO

import pytest
from PIL import Image

from illustrator_mcp.overlay import (
    LABEL_BG_COLOR,
    composite_overlay,
    get_png_dimensions,
    _in_bounds,
    _measure_text,
    _place_pill,
)


def _blank_png(width: int, height: int, color=(255, 255, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _rendered(annotations, width=600, height=400) -> Image.Image:
    out = composite_overlay(_blank_png(width, height), annotations)
    return Image.open(BytesIO(out)).convert("RGB")


def _pill_rect_for(index, annotations, width, height):
    """Recompute where composite_overlay puts a given annotation's pill."""
    from illustrator_mcp.overlay import _compute_font_size, _load_font

    font = _load_font(_compute_font_size(width, height))
    placed = []
    rect = None
    for i, ann in enumerate(annotations):
        left, top, right, bottom = ann["bounds_px"]
        text_w, text_h = _measure_text(font, f"[{ann['label']}]")
        pad = 4
        candidate = _place_pill(
            left, top, right, bottom,
            text_w + pad * 2, text_h + pad * 2,
            width, height, placed,
        )
        placed.append(candidate)
        if i == index:
            rect = candidate
    return rect


class TestLabelsAreNotPaintedOver:
    """A later annotation must not veil an earlier annotation's label."""

    # A small item followed by a large one that covers it completely. The big
    # box's translucent fill is drawn over the small item's label in a
    # single-pass renderer.
    ANNOTATIONS = [
        {"label": "1", "bounds_px": (250, 180, 300, 220)},
        {"label": "2", "bounds_px": (20, 20, 580, 380)},
    ]

    def test_first_label_keeps_its_background_colour(self):
        width, height = 600, 400
        rect = _pill_rect_for(0, self.ANNOTATIONS, width, height)
        assert rect is not None

        rendered = _rendered(self.ANNOTATIONS, width, height)

        # Sample the pill's own background, avoiding the glyphs in the middle.
        sample_x = rect[0] + 2
        sample_y = rect[1] + 2
        pixel = rendered.getpixel((sample_x, sample_y))

        expected = LABEL_BG_COLOR[:3]
        # The pill is composited at alpha 200/255 over white.
        alpha = LABEL_BG_COLOR[3] / 255
        blended = tuple(
            round(channel * alpha + 255 * (1 - alpha)) for channel in expected
        )

        for actual, want in zip(pixel, blended):
            assert abs(actual - want) <= 6, (
                f"label 1's pill reads {pixel}, expected about {blended}. "
                "A later annotation painted over it."
            )

    def test_label_pixels_are_dark_enough_to_read(self):
        """Whatever the exact blend, the pill must stay clearly darker than the page."""
        width, height = 600, 400
        rect = _pill_rect_for(0, self.ANNOTATIONS, width, height)
        rendered = _rendered(self.ANNOTATIONS, width, height)
        pixel = rendered.getpixel((rect[0] + 2, rect[1] + 2))
        assert max(pixel) < 120, f"pill background is too light to read: {pixel}"


class TestPillsStayOnCanvas:
    """A pill drawn past the edge is cropped, and a cropped [N] is unreadable."""

    @pytest.mark.parametrize(
        "bounds",
        [
            (0, 0, 8, 8),            # top-left corner
            (592, 392, 600, 400),    # bottom-right corner
            (596, 0, 600, 4),        # flush against the right edge
            (0, 396, 4, 400),        # flush against the bottom edge
            # Bounds can land outside the canvas: clip_box renders a crop of
            # the artboard, and items may sit off-artboard entirely. Every
            # candidate position is then off-canvas and the label vanishes
            # completely unless it is pulled back inside.
            (700, 500, 800, 600),    # wholly past the bottom-right
            (620, 100, 700, 180),    # past the right edge
            (100, -90, 180, -20),    # above the canvas
            (-50, -50, 650, 450),    # larger than the canvas in both axes
        ],
    )
    def test_pill_fits_inside_the_image(self, bounds):
        width, height = 600, 400
        annotations = [{"label": "199", "bounds_px": bounds}]
        rect = _pill_rect_for(0, annotations, width, height)
        assert _in_bounds(rect, width, height), (
            f"pill {rect} for item at {bounds} hangs outside the {width}x{height} canvas"
        )

    def test_every_label_of_a_dense_render_is_on_canvas(self):
        """Many overlapping items must not push any pill off the edge."""
        width, height = 600, 400
        annotations = [
            {"label": str(i + 1), "bounds_px": (10 + i * 3, 10 + i * 3, 40 + i * 3, 40 + i * 3)}
            for i in range(40)
        ]
        from illustrator_mcp.overlay import _compute_font_size, _load_font

        font = _load_font(_compute_font_size(width, height))
        placed = []
        for ann in annotations:
            left, top, right, bottom = ann["bounds_px"]
            text_w, text_h = _measure_text(font, f"[{ann['label']}]")
            rect = _place_pill(
                left, top, right, bottom,
                text_w + 8, text_h + 8,
                width, height, placed,
            )
            placed.append(rect)
            assert _in_bounds(rect, width, height), f"{ann['label']} placed at {rect}"


class TestRenderStillProducesAValidImage:
    def test_dimensions_are_preserved(self):
        annotations = [{"label": "1", "bounds_px": (10, 10, 100, 100)}]
        out = composite_overlay(_blank_png(600, 400), annotations)
        assert get_png_dimensions(out) == (600, 400)

    def test_empty_annotation_list_is_safe(self):
        out = composite_overlay(_blank_png(200, 200), [])
        assert get_png_dimensions(out) == (200, 200)
