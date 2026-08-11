"""Generate a lightweight PDF for one PiCatchUp recording."""

from __future__ import annotations

import html
from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def create_summary_pdf(
    destination: Path,
    *,
    date: str,
    subject: str | None,
    summary: str,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="PiCatchUp",
    )

    styles = getSampleStyleSheet()

    title = ParagraphStyle(
        "PiCatchUpTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=22,
        leading=26,
        spaceAfter=8,
    )

    meta = ParagraphStyle(
        "PiCatchUpMeta",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        leading=14,
        spaceAfter=16,
    )

    body = ParagraphStyle(
        "PiCatchUpBody",
        parent=styles["BodyText"],
        fontSize=11,
        leading=16,
        spaceAfter=7,
    )

    heading = ParagraphStyle(
        "PiCatchUpHeading",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        spaceBefore=7,
        spaceAfter=7,
    )

    bullet = ParagraphStyle(
        "PiCatchUpBullet",
        parent=body,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=5,
    )

    story = [
        Paragraph("PiCatchUp", title),
        Paragraph(
            f"{html.escape(subject or 'Generale')} &middot; {html.escape(date)}",
            meta,
        ),
        Spacer(1, 4 * mm),
    ]

    for raw in summary.splitlines():
        line = raw.strip()

        if not line:
            story.append(Spacer(1, 2 * mm))
            continue

        if line.startswith("📚"):
            text = line.removeprefix("📚").strip()
            story.append(Paragraph(html.escape(text), heading))
            continue

        if line.startswith("📅"):
            text = line.removeprefix("📅").strip()
            story.append(Paragraph(html.escape(text), heading))
            continue

        if line.startswith("•"):
            text = line.removeprefix("•").strip()
            story.append(
                Paragraph(
                    "&#8226;&nbsp;&nbsp;" + html.escape(text),
                    bullet,
                )
            )
            continue

        story.append(Paragraph(html.escape(line), body))

    doc.build(story)
    return destination
