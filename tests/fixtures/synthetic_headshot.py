"""A synthetic provider head shot, framed like ESPN's 600x436 cut-outs: the
figure's top at row 50, the face below it, a white jersey at the bottom.

`hair` paints a band above the face (ESPN hockey and soccer photos show the
hair there); `cap` paints the navy cap of an MLB photo instead; both None
leaves the face's own colour up to the top, which is a shaved head.
"""

import io

from PIL import Image, ImageDraw


def headshot(face, *, hair=None, cap=None, transparent=True, beard=False, size=(600, 436)):
    """PNG bytes of a head shot with a face of colour `face`."""
    w, h = size
    picture = Image.new("RGBA", size, (0, 0, 0, 0) if transparent else (235, 235, 235, 255))
    draw = ImageDraw.Draw(picture)
    sx, sy = w / 600, h / 436
    box = lambda x0, y0, x1, y1: (x0 * sx, y0 * sy, x1 * sx, y1 * sy)  # noqa: E731
    if hair is None and cap is None:
        draw.rectangle(box(230, 50, 370, 150), fill=(*face, 255))
    draw.ellipse(box(220, 110, 380, 300), fill=(*face, 255))
    if beard:
        draw.rectangle(box(240, 230, 360, 300), fill=(30, 20, 15, 255))
    if hair is not None:
        draw.rectangle(box(215, 50, 385, 130), fill=(*hair, 255))
    if cap is not None:
        draw.rectangle(box(215, 50, 385, 150), fill=(*cap, 255))
    draw.rectangle(box(60, 330, 540, 436), fill=(250, 250, 250, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()
