"""
Per-candidate PDF report.

This is the artefact that leaves the system. It gets forwarded to a hiring
manager, attached to an email, and read by someone who never saw the interface
or its caveats. So it carries the same discipline the UI does, and a little more:

* **Every requirement shows its evidence.** A quoted span from the resume, or an
  explicit statement of what was searched for and not found. Never a bare score.
* **The coverage meter is drawn, not described.** One tick per requirement, the
  same signature element as the interface, so the count reads as countable.
* **Gaps are framed as questions.** "Worth asking about", not "deficiencies".
* **Provenance is on every page.** Scorer version, batch id, generation date. A
  ranking that cannot be traced back is not defensible six months later.
* **Identity is opt-in.** The report is anonymous unless it was generated with
  blind screening off, and that choice is recorded in the audit trail.

Rendered with reportlab. Note the guidance in the pdf skill: never use Unicode
subscript or superscript characters with the built-in fonts — they render as
black boxes. Nothing here needs them.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Palette lifted from the interface tokens so the report and the screen agree.
INK = colors.HexColor("#12161F")
INK2 = colors.HexColor("#3F4757")
MUTED = colors.HexColor("#6B7484")
RULE = colors.HexColor("#C9D0DA")
BLUE = colors.HexColor("#2456E6")
GREEN = colors.HexColor("#12805C")
GREEN_LO = colors.HexColor("#E8F5F0")
AMBER = colors.HexColor("#A65F00")
AMBER_LO = colors.HexColor("#FDF3E3")
SLATE = colors.HexColor("#8A93A3")
HOLLOW = colors.HexColor("#DDE3EC")
QUOTE_BG = colors.HexColor("#F5F8FC")

TIER_COLOUR = {"meets_all": GREEN, "one_short": AMBER, "multiple_gaps": SLATE}
VERDICT_COLOUR = {"met": GREEN, "partial": AMBER, "not_met": SLATE}
VERDICT_LABEL = {
    "met": "Met", "partial": "Partial evidence", "not_met": "No evidence found",
}


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "h1", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=17, leading=21, textColor=INK, spaceAfter=1,
        ),
        "sub": ParagraphStyle(
            "sub", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=2,
        ),
        "section": ParagraphStyle(
            "section", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9, leading=12, textColor=INK, spaceBefore=14, spaceAfter=5,
        ),
        "req": ParagraphStyle(
            "req", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.6, leading=12.6, textColor=INK, spaceAfter=2,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9, leading=12.8, textColor=INK2,
            leftIndent=8, rightIndent=6, spaceBefore=2, spaceAfter=2,
        ),
        "absence": ParagraphStyle(
            "absence", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=12.6, textColor=INK2, leftIndent=8, spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontName="Helvetica",
            fontSize=7.8, leading=10.5, textColor=MUTED, leftIndent=8,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.2, leading=13, textColor=INK2, alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "note": ParagraphStyle(
            "note", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.2, leading=11.6, textColor=MUTED, spaceBefore=3,
        ),
        "question": ParagraphStyle(
            "question", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.4, leading=13, textColor=INK, leftIndent=8, spaceAfter=2,
        ),
    }


class CoverageMeter(Flowable):
    """
    The segmented meter, drawn rather than described.

    One tick per must-have requirement — green met, amber partial, hollow none.
    Deliberately not a progress bar: a bar filled to 89% invites the reader to
    treat it as a measurement, while eight of nine lit segments makes the
    missing one the thing you notice.
    """

    def __init__(self, verdicts: list[str], width: float = 150, height: float = 9):
        Flowable.__init__(self)
        self.verdicts = verdicts or []
        self.width = width
        self.height = height

    def draw(self):
        count = len(self.verdicts)
        if not count:
            return
        gap = 2.4
        tick = (self.width - gap * (count - 1)) / count
        x = 0
        for verdict in self.verdicts:
            colour = {
                "met": GREEN, "partial": AMBER,
            }.get(verdict, HOLLOW)
            self.canv.setFillColor(colour)
            self.canv.roundRect(x, 0, tick, self.height, 1.6, stroke=0, fill=1)
            x += tick + gap


def _badge(text: str, colour, background) -> Table:
    """A small pill, matching the interface badges."""
    cell = Paragraph(
        f'<font size="7.6" color="{colour.hexval()}"><b>{text}</b></font>',
        ParagraphStyle("badge", fontName="Helvetica-Bold", leading=10),
    )
    table = Table([[cell]], colWidths=[len(text) * 4.6 + 14], rowHeights=[13])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return table


def _rule(width: float = 165 * mm) -> Table:
    line = Table([[""]], colWidths=[width], rowHeights=[0.6])
    line.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RULE)]))
    return line


def _escape(value: str | None) -> str:
    """reportlab Paragraphs parse a small XML dialect; resume text is not XML."""
    if not value:
        return ""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _requirement_block(item: dict, styles: dict) -> list:
    """One requirement: its verdict, and the evidence behind that verdict."""
    verdict = item.get("verdict", "not_met")
    colour = VERDICT_COLOUR.get(verdict, SLATE)
    background = {
        "met": GREEN_LO, "partial": AMBER_LO,
    }.get(verdict, colors.HexColor("#F2F4F8"))

    header = Table(
        [[
            Paragraph(_escape(item.get("text", "")), styles["req"]),
            _badge(VERDICT_LABEL.get(verdict, "Unknown"), colour, background),
        ]],
        colWidths=[118 * mm, 47 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))

    block = [header]
    quote = item.get("quote")

    if quote:
        body = Table(
            [[Paragraph(f"&#8220;{_escape(quote)}&#8221;", styles["quote"])]],
            colWidths=[165 * mm],
        )
        body.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBEFORE", (0, 0), (0, -1), 2, colour),
        ]))
        block.append(body)

        bits = []
        if item.get("confidence_label"):
            bits.append(_escape(item["confidence_label"]))
        if item.get("page"):
            bits.append(f"page {item['page']}")
        if item.get("method"):
            bits.append(f"matched by {_escape(item['method']).replace('_', ' ')}")
        if item.get("quote_validated") is False:
            bits.append("quote could not be verified against the source")
        if bits:
            block.append(Paragraph("  ·  ".join(bits), styles["meta"]))
    else:
        statement = item.get("absence_statement") or (
            "No supporting text was found for this requirement."
        )
        block.append(Paragraph(_escape(statement), styles["absence"]))
        terms = item.get("search_terms") or []
        if terms:
            block.append(Paragraph(
                "Terms searched: " + _escape(", ".join(terms)), styles["meta"],
            ))

    override = item.get("override")
    if override:
        block.append(Paragraph(
            f"<b>Recruiter override:</b> {_escape(override.get('verdict', ''))}"
            + (f" — {_escape(override.get('reason'))}" if override.get("reason") else "")
            + f"  (system verdict was {_escape(item.get('ai_verdict', ''))}, retained)",
            styles["meta"],
        ))

    block.append(Spacer(1, 7))
    return block


def build_candidate_report(
    detail: dict,
    *,
    job_title: str,
    screening_name: str,
    batch_id: str,
    blind: bool = True,
) -> bytes:
    """
    Render one candidate's report.

    `detail` is the same payload the candidate detail endpoint returns, so the
    report and the screen are guaranteed to agree — there is no second code path
    that could drift.
    """
    styles = _styles()
    buffer = io.BytesIO()

    score = detail.get("score") or {}
    coverage = detail.get("coverage") or []
    musts = [c for c in coverage if c.get("necessity") == "must_have"]
    nices = [c for c in coverage if c.get("necessity") == "nice_to_have"]
    gaps = [c for c in musts if c.get("verdict") != "met"]

    display_name = detail.get("display_name") or f"Candidate #{detail.get('reference')}"
    generated = datetime.now(UTC)

    def furniture(canvas, doc):
        """Header rule and footer provenance on every page."""
        canvas.saveState()
        canvas.setFont("Helvetica", 7.2)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            20 * mm, 12 * mm,
            f"ResumeIQ  ·  {screening_name}  ·  scorer {score.get('scorer_version', 'n/a')}"
            f"  ·  generated {generated:%d %b %Y %H:%M} UTC",
        )
        canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
        if not blind:
            canvas.setFillColor(AMBER)
            canvas.drawString(20 * mm, 8 * mm, "Contains candidate identity — handle accordingly.")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=22 * mm,
        title=f"{display_name} — {job_title}",
        author="ResumeIQ",
        subject=f"Screening report, scorer {score.get('scorer_version', 'n/a')}",
    )

    story: list = []

    # --- header -----------------------------------------------------------
    story.append(Paragraph(_escape(display_name), styles["h1"]))
    line = [detail.get("title")]
    if detail.get("experience_years") is not None:
        line.append(f"{detail['experience_years']} yrs experience")
    if not blind and detail.get("identity"):
        identity = detail["identity"]
        contact = [identity.get("email"), identity.get("phone")]
        line.extend([c for c in contact if c])
    story.append(Paragraph(
        _escape("  ·  ".join(str(p) for p in line if p)), styles["sub"],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        _escape(f"Screened against: {job_title}"), styles["sub"],
    ))
    story.append(Spacer(1, 9))
    story.append(_rule())
    story.append(Spacer(1, 9))

    # --- coverage summary -------------------------------------------------
    if score:
        tier = score.get("coverage_tier", "multiple_gaps")
        summary = Table(
            [[
                Paragraph(
                    f'<font size="19" color="{INK.hexval()}"><b>'
                    f'{score.get("must_haves_met", 0)}</b></font>'
                    f'<font size="12" color="{MUTED.hexval()}"> / '
                    f'{score.get("must_haves_total", 0)}</font><br/>'
                    f'<font size="7.6" color="{MUTED.hexval()}">MUST-HAVES MET</font>',
                    styles["body"],
                ),
                CoverageMeter([c.get("verdict", "not_met") for c in musts], width=62 * mm),
                _badge(
                    score.get("match_level", ""),
                    TIER_COLOUR.get(tier, SLATE),
                    {"meets_all": GREEN_LO, "one_short": AMBER_LO}.get(
                        tier, colors.HexColor("#F2F4F8")
                    ),
                ),
                Paragraph(
                    f'<font size="19" color="{INK.hexval()}"><b>'
                    f'{round(score.get("final_score", 0))}</b></font><br/>'
                    f'<font size="7.6" color="{MUTED.hexval()}">RANKING SCORE</font>',
                    styles["body"],
                ),
            ]],
            colWidths=[30 * mm, 66 * mm, 39 * mm, 30 * mm],
        )
        summary.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("ALIGN", (3, 0), (3, 0), "RIGHT"),
        ]))
        story.append(summary)
        story.append(Spacer(1, 6))
        story.append(Paragraph(_escape(score.get("tier_note", "")), styles["note"]))
        story.append(Spacer(1, 4))

    # --- the disclaimer, near the top where it will actually be read ------
    disclaimer = Table(
        [[Paragraph(
            "<b>How to read this report.</b> Every requirement below shows either "
            "the exact resume text supporting it, or a statement of what was "
            "searched for and not found. A gap means the resume did not mention "
            "something — not that the candidate lacks it. This score ranks a "
            "resume against one job description. It is not a hiring "
            "recommendation and not a prediction of job performance.",
            styles["note"],
        )]],
        colWidths=[165 * mm],
    )
    disclaimer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBEFORE", (0, 0), (0, -1), 2, BLUE),
    ]))
    story.append(Spacer(1, 4))
    story.append(disclaimer)

    # --- must-have coverage ----------------------------------------------
    story.append(Paragraph("MUST-HAVE REQUIREMENTS", styles["section"]))
    story.append(_rule())
    story.append(Spacer(1, 6))
    for item in musts:
        story.append(KeepTogether(_requirement_block(item, styles)))

    # --- nice-to-have -----------------------------------------------------
    if nices:
        story.append(Paragraph("NICE-TO-HAVE", styles["section"]))
        story.append(_rule())
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            "These separate candidates who already meet every must-have. They "
            "cannot compensate for a missing must-have.", styles["note"],
        ))
        story.append(Spacer(1, 6))
        for item in nices:
            story.append(KeepTogether(_requirement_block(item, styles)))

    # --- gaps and questions ----------------------------------------------
    questions = detail.get("questions") or []
    if gaps or questions:
        story.append(PageBreak())
        story.append(Paragraph("WORTH ASKING ABOUT", styles["section"]))
        story.append(_rule())
        story.append(Spacer(1, 5))
        story.append(Paragraph(
            f"{len(gaps)} must-have requirement{'s' if len(gaps) != 1 else ''} "
            "without direct evidence in the submitted resume. These are "
            "interview topics, not conclusions.", styles["note"],
        ))
        story.append(Spacer(1, 8))

        for gap in gaps:
            story.append(Paragraph(_escape(gap.get("text", "")), styles["req"]))
            story.append(Paragraph(
                _escape(gap.get("absence_statement")
                        or "Evidence found is indirect or hedged."),
                styles["absence"],
            ))
            story.append(Spacer(1, 6))

        if questions:
            story.append(Paragraph("SUGGESTED SCREENING QUESTIONS", styles["section"]))
            story.append(_rule())
            story.append(Spacer(1, 6))
            for question in questions:
                block = [
                    Paragraph(
                        _escape(question.get("rationale") or ""), styles["meta"]
                    ),
                    Paragraph(
                        f"&#8220;{_escape(question.get('question', ''))}&#8221;",
                        styles["question"],
                    ),
                    Spacer(1, 7),
                ]
                story.append(KeepTogether(block))

    # --- decision trail ---------------------------------------------------
    trail = detail.get("decision_trail") or []
    if trail:
        story.append(Paragraph("DECISION TRAIL", styles["section"]))
        story.append(_rule())
        story.append(Spacer(1, 5))
        rows = [[
            Paragraph(f"<b>{_escape(d.get('action', ''))}</b>", styles["body"]),
            Paragraph(_escape(d.get("coverage") or ""), styles["body"]),
            Paragraph(_escape(d.get("note") or ""), styles["body"]),
            Paragraph(
                _escape((d.get("at") or "")[:16].replace("T", " ")), styles["body"]
            ),
        ] for d in trail]
        table = Table(rows, colWidths=[30 * mm, 20 * mm, 82 * mm, 33 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(table)

    # --- provenance -------------------------------------------------------
    story.append(Spacer(1, 12))
    story.append(_rule())
    story.append(Spacer(1, 5))
    story.append(Paragraph(
        f"Screening batch {batch_id}  ·  scorer version "
        f"{score.get('scorer_version', 'n/a')}  ·  "
        f"generated {generated:%d %B %Y at %H:%M} UTC.<br/>"
        "This ranking can be reproduced from stored evidence at any time. "
        "Every recruiter decision is recorded in the audit trail.",
        styles["note"],
    ))

    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()
