from __future__ import annotations

import uuid
from io import BytesIO

import fitz
from PIL import Image, ImageChops, ImageDraw
import pytest

from app.models.signature_field import SignatureField
from app.services.pdf_service import stamp_signatures_on_pdf


def _make_signature_png() -> bytes:
    image = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 39, 39), fill=(255, 0, 0, 255))
    draw.rectangle((40, 0, 79, 39), fill=(0, 0, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _make_rotated_cropped_pdf(rotation: int) -> bytes:
    document = fitz.open()
    page = document.new_page(width=800, height=1000)
    page.set_cropbox(fitz.Rect(80, 100, 700, 880))
    page.set_rotation(rotation)
    pdf_bytes = document.tobytes()
    document.close()
    return pdf_bytes


def _color_bounds(
    pixmap: fitz.Pixmap,
    *,
    color: str,
) -> tuple[int, int, int, int]:
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    red, green, blue = image.split()
    high = [255 if value > 200 else 0 for value in range(256)]
    low = [255 if value < 50 else 0 for value in range(256)]
    if color == "red":
        mask = ImageChops.multiply(
            ImageChops.multiply(red.point(high), green.point(low)),
            blue.point(low),
        )
    else:
        mask = ImageChops.multiply(
            ImageChops.multiply(blue.point(high), red.point(low)),
            green.point(low),
        )
    bounds = mask.getbbox()
    assert bounds is not None
    return bounds


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_stamping_respects_rotation_and_nonzero_cropbox(rotation: int) -> None:
    field = SignatureField(
        document_id=uuid.uuid4(),
        page_number=1,
        x_norm=0.2,
        y_norm=0.25,
        width_norm=0.3,
        height_norm=0.2,
        label="Signature",
        required=True,
    )

    stamped_pdf = stamp_signatures_on_pdf(
        _make_rotated_cropped_pdf(rotation),
        [(field, _make_signature_png())],
    )

    with fitz.open(stream=stamped_pdf, filetype="pdf") as signed_document:
        page = signed_document[0]
        pixmap = page.get_pixmap(alpha=False)
        red_bounds = _color_bounds(pixmap, color="red")
        blue_bounds = _color_bounds(pixmap, color="blue")
        expected_center = (
            (field.x_norm + field.width_norm / 2) * page.rect.width,
            (field.y_norm + field.height_norm / 2) * page.rect.height,
        )
        red_center = (
            (red_bounds[0] + red_bounds[2]) / 2,
            (red_bounds[1] + red_bounds[3]) / 2,
        )
        blue_center = (
            (blue_bounds[0] + blue_bounds[2]) / 2,
            (blue_bounds[1] + blue_bounds[3]) / 2,
        )

    assert abs((red_center[0] + blue_center[0]) / 2 - expected_center[0]) <= 3
    assert abs((red_center[1] + blue_center[1]) / 2 - expected_center[1]) <= 3
    assert red_center[0] < blue_center[0]
