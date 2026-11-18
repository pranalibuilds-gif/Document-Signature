from __future__ import annotations

import hashlib
from dataclasses import dataclass

import fitz

from app.models.signature_field import SignatureField


class PdfInspectionError(ValueError):
    """Raised when an uploaded file is not an acceptable PDF."""


class PdfStampingError(ValueError):
    """Raised when a PDF cannot be safely stamped."""


@dataclass(frozen=True)
class PdfInspectionResult:
    page_count: int
    sha256_hex: str
    file_size_bytes: int


def inspect_pdf_bytes(data: bytes) -> PdfInspectionResult:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except (fitz.FileDataError, RuntimeError) as exc:
        raise PdfInspectionError("Invalid or corrupted PDF file") from exc

    with document:
        if document.is_encrypted or document.needs_pass:
            raise PdfInspectionError(
                "Encrypted or password-protected PDFs are not supported"
            )
        if not document.is_pdf or document.page_count < 1:
            raise PdfInspectionError("Invalid or corrupted PDF file")
        page_count = document.page_count

    return PdfInspectionResult(
        page_count=page_count,
        sha256_hex=hashlib.sha256(data).hexdigest(),
        file_size_bytes=len(data),
    )


def _signature_rect(page: fitz.Page, field: SignatureField) -> fitz.Rect:
    cropbox = page.cropbox or page.rect
    media_height = page.mediabox.height
    crop_x0 = cropbox.x0
    crop_x1 = cropbox.x1
    crop_y0 = media_height - cropbox.y1
    crop_y1 = media_height - cropbox.y0

    view_x0 = field.x_norm * page.rect.width
    view_y0 = field.y_norm * page.rect.height
    view_x1 = (field.x_norm + field.width_norm) * page.rect.width
    view_y1 = (field.y_norm + field.height_norm) * page.rect.height

    if page.rotation == 0:
        pdf_rect = fitz.Rect(
            crop_x0 + view_x0,
            crop_y1 - view_y1,
            crop_x0 + view_x1,
            crop_y1 - view_y0,
        )
    elif page.rotation == 90:
        pdf_rect = fitz.Rect(
            crop_x0 + view_y0,
            crop_y0 + view_x0,
            crop_x0 + view_y1,
            crop_y0 + view_x1,
        )
    elif page.rotation == 180:
        pdf_rect = fitz.Rect(
            crop_x1 - view_x1,
            crop_y0 + view_y0,
            crop_x1 - view_x0,
            crop_y0 + view_y1,
        )
    elif page.rotation == 270:
        pdf_rect = fitz.Rect(
            crop_x1 - view_y1,
            crop_y1 - view_x1,
            crop_x1 - view_y0,
            crop_y1 - view_x0,
        )
    else:
        raise PdfStampingError(f"Unsupported page rotation: {page.rotation}")

    return pdf_rect * page.transformation_matrix


def stamp_signatures_on_pdf(
    original_pdf_bytes: bytes,
    fields_and_signatures: list[tuple[SignatureField, bytes]],
) -> bytes:
    try:
        document = fitz.open(stream=original_pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, RuntimeError) as exc:
        raise PdfStampingError("Stored PDF is invalid or corrupted") from exc

    with document:
        if document.is_encrypted or document.needs_pass:
            raise PdfStampingError("Encrypted PDFs cannot be stamped")
        if not document.is_pdf or document.page_count < 1:
            raise PdfStampingError("Stored PDF is invalid or corrupted")

        try:
            for field, signature_png in fields_and_signatures:
                if field.page_number < 1 or field.page_number > document.page_count:
                    raise PdfStampingError(
                        "Signature field page exceeds the PDF page count"
                    )
                page = document[field.page_number - 1]
                page.insert_image(
                    _signature_rect(page, field),
                    stream=signature_png,
                    keep_proportion=True,
                    overlay=True,
                    rotate=page.rotation,
                )
            return document.tobytes(garbage=4, deflate=True)
        except PdfStampingError:
            raise
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise PdfStampingError("Could not stamp the signing image onto the PDF") from exc
