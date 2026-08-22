"""Render the insurance schedule as a PDF.

The document has to stand on its own in an underwriter's hands, so it states
the vessel it covers, the period, what is included, and — explicitly — what has
been left out and why. A schedule that silently omits categories invites the
question of what else it omitted.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..db.database import fmt_money

INK = colors.HexColor("#1a1a1a")
DIM = colors.HexColor("#666666")
RULE = colors.HexColor("#cccccc")
BAND = colors.HexColor("#f2f4f6")

VESSEL_FIELDS = [
    ("vessel_name", "Vessel name"),
    ("vessel_make_model", "Make / model"),
    ("hin", "Hull identification number"),
    ("registration_mark", "Registration mark"),
    ("registration", "Registration"),
    ("vessel_type", "Type"),
]


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=17, leading=21, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica",
                              fontSize=10, leading=14, textColor=DIM),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=INK,
                             spaceBefore=16, spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9, leading=12, textColor=INK),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontName="Helvetica",
                                fontSize=8, leading=11, textColor=DIM),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName="Helvetica",
                               fontSize=8.5, leading=11, textColor=INK),
        "num": ParagraphStyle("n", parent=base["Normal"], fontName="Helvetica",
                              fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
    }


def _detail_table(groups: list[dict], st: dict) -> list:
    flow: list = []
    for group in groups:
        rows = [[
            Paragraph("<b>Date</b>", st["cell"]), Paragraph("<b>Item</b>", st["cell"]),
            Paragraph("<b>Vendor</b>", st["cell"]), Paragraph("<b>Ref</b>", st["cell"]),
            Paragraph("<b>Amount</b>", st["num"]),
        ]]
        for item in group["items"]:
            qty = f' ×{item["quantity"]:g}' if item["quantity"] and item["quantity"] != 1 else ""
            rows.append([
                Paragraph(item["date"] or "—", st["cell"]),
                Paragraph(f'{item["description"]}{qty}', st["cell"]),
                Paragraph(item["vendor"] or "—", st["cell"]),
                Paragraph(item["reference"] or "—", st["cell"]),
                Paragraph(fmt_money(item["total_cents"]), st["num"]),
            ])
        rows.append([
            Paragraph("", st["cell"]), Paragraph("", st["cell"]), Paragraph("", st["cell"]),
            Paragraph("<b>Subtotal</b>", st["num"]),
            Paragraph(f'<b>{fmt_money(group["total_cents"])}</b>', st["num"]),
        ])

        table = Table(rows, colWidths=[0.75*inch, 3.35*inch, 1.35*inch, 0.75*inch, 0.9*inch],
                      repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
            ("LINEABOVE", (0, -1), (-1, -1), 0.6, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafbfc")]),
        ]))
        flow.append(KeepTogether([Paragraph(group["name"], st["h2"]), table]))
    return flow


def render(report: dict, out_path: Path | str, prepared_on: str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()

    doc = SimpleDocTemplate(
        str(out_path), pagesize=LETTER,
        leftMargin=0.7*inch, rightMargin=0.7*inch,
        topMargin=0.7*inch, bottomMargin=0.7*inch,
        title=f"Equipment & Installation Schedule — {report['vessel']}",
        author="Ophelia's Key project records",
    )

    flow: list = [
        Paragraph("Schedule of Equipment and Professional Installation", st["title"]),
        Paragraph(report["vessel"], st["sub"]),
        Spacer(1, 14),
    ]

    # --- vessel identity ---
    meta = report["meta"]
    ident = [[Paragraph(f"<b>{label}</b>", st["cell"]), Paragraph(meta[key], st["cell"])]
             for key, label in VESSEL_FIELDS if meta.get(key)]
    period = "—"
    if report["period_start"]:
        period = f'{report["period_start"]} to {report["period_end"]}'
    ident.append([Paragraph("<b>Period covered</b>", st["cell"]), Paragraph(period, st["cell"])])
    ident.append([Paragraph("<b>Prepared</b>", st["cell"]), Paragraph(prepared_on, st["cell"])])

    identity = Table(ident, colWidths=[1.9*inch, 5.2*inch])
    identity.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flow += [identity, Spacer(1, 16)]

    # --- summary ---
    summary = Table([
        [Paragraph("<b>Equipment</b>", st["cell"]),
         Paragraph(f'<b>{fmt_money(report["equipment_total_cents"])}</b>', st["num"])],
        [Paragraph("<b>Professional installation</b>", st["cell"]),
         Paragraph(f'<b>{fmt_money(report["installation_total_cents"])}</b>', st["num"])],
        [Paragraph("<b>Total claimed</b>", st["cell"]),
         Paragraph(f'<b>{fmt_money(report["total_cents"])}</b>', st["num"])],
    ], colWidths=[5.2*inch, 1.9*inch])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEABOVE", (0, 2), (-1, 2), 0.8, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    flow += [summary, Spacer(1, 6),
             Paragraph(f'{report["item_count"]} line items across '
                       f'{len(report["equipment"]) + len(report["installation"])} systems.',
                       st["small"])]

    if report["equipment"]:
        flow.append(Paragraph("Equipment", st["h2"]))
        flow += _detail_table(report["equipment"], st)

    if report["installation"]:
        flow.append(Paragraph("Professional installation", st["h2"]))
        flow += _detail_table(report["installation"], st)

    # --- what is deliberately not here ---
    if report["excluded"]:
        flow.append(PageBreak())
        flow.append(Paragraph("Excluded from this schedule", st["h2"]))
        flow.append(Paragraph(
            "The following project costs are recorded but deliberately omitted, as they "
            "add no insurable property to the vessel. They are listed so the schedule "
            "states its own boundaries.", st["body"]))
        flow.append(Spacer(1, 8))
        rows = [[Paragraph("<b>Category</b>", st["cell"]),
                 Paragraph("<b>Reason for exclusion</b>", st["cell"]),
                 Paragraph("<b>Amount</b>", st["num"])]]
        for entry in report["excluded"]:
            rows.append([
                Paragraph(entry["name"], st["cell"]),
                Paragraph(entry["reason"], st["cell"]),
                Paragraph(fmt_money(entry["total_cents"]), st["num"]),
            ])
        rows.append([Paragraph("", st["cell"]), Paragraph("<b>Total excluded</b>", st["num"]),
                     Paragraph(f'<b>{fmt_money(report["excluded_total_cents"])}</b>', st["num"])])
        excl = Table(rows, colWidths=[1.7*inch, 4.5*inch, 0.9*inch], repeatRows=1)
        excl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
            ("LINEABOVE", (0, -1), (-1, -1), 0.6, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        flow.append(excl)

    flow += [
        Spacer(1, 18),
        Paragraph(
            "Amounts are as recorded from vendor invoices and order confirmations. "
            "Figures are actual cost incurred, not appraised or replacement value.",
            st["small"]),
    ]

    doc.build(flow)
    return out_path
