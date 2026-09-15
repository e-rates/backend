"""
AI Report Generator Module for E-Rates.
Generates executive analysis PDFs and statutory report PDFs with ReportLab.
Files are persisted in settings.MEDIA_ROOT / 'ai_reports' for secure download.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from . import rate_reports, report_builders
from .models import Payment, Parcel

NAIROBI = ZoneInfo('Africa/Nairobi')


def _ensure_report_dir() -> Path:
    target_dir = Path(settings.MEDIA_ROOT) / 'ai_reports'
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _today():
    return timezone.now().astimezone(NAIROBI).date()


def _fmt_money(amount) -> str:
    try:
        val = Decimal(amount or 0)
        return f"KES {val:,.2f}"
    except Exception:
        return f"KES {amount}"


def _fmt_int(num) -> str:
    try:
        return f"{int(num or 0):,}"
    except Exception:
        return str(num or 0)


def build_executive_analysis_pdf(year: int = None, ward: str = None, county: str = None, generated_by: str = 'AI Assistant') -> dict:
    """
    Builds a high-level Land Rates Executive Analysis & Intelligence Briefing PDF.
    Returns metadata including file path, download URL, and analytical summary.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether, HRFlowable

    if not year:
        year = _today().year
    county_name = (county or getattr(settings, 'COUNTY_NAME', 'Kenya')).strip()

    # 1. Fetch live metrics
    totals = rate_reports.collections(year, county=county)
    all_wards = rate_reports.wards(year, county=county)
    if ward:
        selected_wards = [w for w in all_wards if w['ward'].lower() == ward.lower()]
    else:
        selected_wards = all_wards

    billed = sum((Decimal(w['outstanding']) + Decimal(w['collected']) for w in selected_wards), Decimal(0))
    collected = sum((Decimal(w['collected']) for w in selected_wards), Decimal(0))
    outstanding = sum((Decimal(w['outstanding']) for w in selected_wards), Decimal(0))
    total_parcels = sum((w['parcels'] for w in selected_wards), 0)
    overdue_count = sum((w['overdue'] for w in selected_wards), 0)
    overdue_amount = sum((Decimal(w.get('overdue_amount') or 0) for w in selected_wards), Decimal(0))

    rate_percent = round(Decimal(collected / billed * 100), 1) if billed > 0 else Decimal('0.0')

    # Sort wards by collection rate
    def ward_rate(w):
        b = Decimal(w['outstanding']) + Decimal(w['collected'])
        return round(float(Decimal(w['collected']) / b * 100), 1) if b > 0 else 0.0

    wards_ranked = sorted(selected_wards, key=ward_rate, reverse=True)
    top_performers = wards_ranked[:3]
    lagging_performers = sorted(selected_wards, key=ward_rate)[:3]

    # Arrears aging
    arrears_rep = report_builders.arrears_report(_today(), county=county)
    aging_rows = arrears_rep.sections[0].rows if arrears_rep.sections else []

    # 2. Setup ReportLab styles
    ink = colors.HexColor('#0f172a')         # Slate 900
    subtle = colors.HexColor('#475569')      # Slate 600
    muted = colors.HexColor('#64748b')       # Slate 500
    rule = colors.HexColor('#cbd5e1')        # Slate 300
    brand = colors.HexColor('#1e3a8a')       # Navy / Government blue
    band = colors.HexColor('#f8fafc')        # Slate 50

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=brand,
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=subtle,
    )
    section_h2 = ParagraphStyle(
        'SectionH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=brand,
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        textColor=ink,
    )
    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=ink,
    )
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell,
        fontName='Helvetica-Bold',
    )
    table_cell_right = ParagraphStyle(
        'TableCellRight',
        parent=table_cell,
        alignment=2,  # Right
    )
    table_cell_right_bold = ParagraphStyle(
        'TableCellRightBold',
        parent=table_cell_bold,
        alignment=2,
    )

    # 3. Running header & footer
    today_str = _today().strftime('%d %B %Y')
    gen_time_str = timezone.now().astimezone(NAIROBI).strftime('%H:%M:%S EAT')
    doc_title = f"{county_name.upper()} COUNTY · LAND RATES EXECUTIVE ANALYSIS"

    def frame(canvas, doc):
        canvas.saveState()
        width, height = A4
        # Top rule and header
        canvas.setFont('Helvetica-Bold', 8)
        canvas.setFillColor(brand)
        canvas.drawString(14 * mm, height - 10 * mm, f"REPUBLIC OF KENYA · {county_name.upper()} COUNTY GOVERNMENT")
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(muted)
        canvas.drawRightString(width - 14 * mm, height - 10 * mm, "LAND RATES REVENUE INTELLIGENCE UNIT")
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.5)
        canvas.line(14 * mm, height - 12 * mm, width - 14 * mm, height - 12 * mm)

        # Footer
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(14 * mm, 8 * mm, f"Generated {today_str} {gen_time_str} by {generated_by} · E-Rates System")
        canvas.drawRightString(width - 14 * mm, 8 * mm, f"Page {doc.page}")
        canvas.line(14 * mm, 11 * mm, width - 14 * mm, 11 * mm)
        canvas.restoreState()

    out = BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=15 * mm,
        title=doc_title,
        author=f"{county_name} County Government",
    )

    story = []

    # Title block
    filter_desc = f"Specific to {ward.title()} Ward" if ward else f"County-wide Overview ({len(selected_wards)} Wards)"
    story.append(Paragraph(f"Land Rates Revenue Performance & Risk Analysis ({year})", title_style))
    story.append(Spacer(1, 1 * mm))
    story.append(Paragraph(
        f"<b>Rating Year:</b> {year} &nbsp;|&nbsp; <b>Jurisdiction:</b> {county_name} County &nbsp;|&nbsp; <b>Scope:</b> {filter_desc} &nbsp;|&nbsp; <b>Date:</b> {today_str}",
        subtitle_style
    ))
    story.append(Spacer(1, 3 * mm))

    # Executive Briefing
    exec_text = (
        f"This executive intelligence briefing synthesizes valuation, billing, payment settlement, and arrears risk "
        f"for {county_name} County during the {year} rating cycle. As of {today_str}, total billed revenue stands at "
        f"<b>{_fmt_money(billed)}</b> across <b>{_fmt_int(total_parcels)}</b> registered parcels. "
        f"Settled revenue reached <b>{_fmt_money(collected)}</b>, reflecting an overall collection efficiency rate of "
        f"<b>{rate_percent}%</b>. Uncollected exposure totals <b>{_fmt_money(outstanding)}</b>, of which "
        f"<b>{_fmt_money(overdue_amount)}</b> is actively overdue across <b>{_fmt_int(overdue_count)}</b> default accounts."
    )
    story.append(Paragraph(exec_text, body_style))
    story.append(Spacer(1, 3 * mm))

    # 4. KPI Scorecards (4-box table)
    box_w = (A4[0] - 28 * mm) / 4.0
    kpi_data = [
        [
            Paragraph("<b>TOTAL BILLED</b>", ParagraphStyle('k1', parent=table_cell, textColor=subtle, fontSize=6.5)),
            Paragraph("<b>COLLECTED REVENUE</b>", ParagraphStyle('k2', parent=table_cell, textColor=subtle, fontSize=6.5)),
            Paragraph("<b>COLLECTION EFFICIENCY</b>", ParagraphStyle('k3', parent=table_cell, textColor=subtle, fontSize=6.5)),
            Paragraph("<b>OVERDUE / ARREARS</b>", ParagraphStyle('k4', parent=table_cell, textColor=subtle, fontSize=6.5)),
        ],
        [
            Paragraph(f"<b>{_fmt_money(billed)}</b>", ParagraphStyle('kv1', parent=table_cell_bold, fontSize=9.5, textColor=ink)),
            Paragraph(f"<b>{_fmt_money(collected)}</b>", ParagraphStyle('kv2', parent=table_cell_bold, fontSize=9.5, textColor=colors.HexColor('#166534'))),
            Paragraph(f"<b>{rate_percent}%</b>", ParagraphStyle('kv3', parent=table_cell_bold, fontSize=11, textColor=brand)),
            Paragraph(f"<b>{_fmt_money(overdue_amount)}</b>", ParagraphStyle('kv4', parent=table_cell_bold, fontSize=9.5, textColor=colors.HexColor('#991b1b'))),
        ],
        [
            Paragraph(f"{_fmt_int(total_parcels)} total rateable parcels", table_cell),
            Paragraph(f"KES {totals.get('collected_today', 0):,.2f} collected today", table_cell),
            Paragraph("Compliance target: 85.0%", table_cell),
            Paragraph(f"{_fmt_int(overdue_count)} accounts past deadline", table_cell),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[box_w] * 4, hAlign='LEFT')
    kpi_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, rule),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, rule),
        ('BACKGROUND', (0, 0), (-1, 0), band),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 4 * mm))

    # 5. Ward Performance Table
    story.append(Paragraph("1. Ward Collection Performance & Compliance", section_h2))
    table_w = A4[0] - 28 * mm
    w_cols = [table_w * 0.22, table_w * 0.18, table_w * 0.10, table_w * 0.16, table_w * 0.16, table_w * 0.18]
    ward_table_data = [[
        Paragraph("<b>Ward Name</b>", table_cell_bold),
        Paragraph("<b>Sub-County</b>", table_cell_bold),
        Paragraph("<b>Parcels</b>", table_cell_right_bold),
        Paragraph("<b>Billed (KES)</b>", table_cell_right_bold),
        Paragraph("<b>Collected (KES)</b>", table_cell_right_bold),
        Paragraph("<b>Collection %</b>", table_cell_right_bold),
    ]]

    for w in selected_wards[:16]:
        b = Decimal(w['outstanding']) + Decimal(w['collected'])
        pct = round(float(Decimal(w['collected']) / b * 100), 1) if b > 0 else 0.0
        pct_color = '#166534' if pct >= 70 else ('#b45309' if pct >= 40 else '#b91c1c')
        ward_table_data.append([
            Paragraph(w['ward'], table_cell),
            Paragraph(w.get('sub_county') or '-', table_cell),
            Paragraph(_fmt_int(w['parcels']), table_cell_right),
            Paragraph(f"{b:,.2f}", table_cell_right),
            Paragraph(f"{Decimal(w['collected']):,.2f}", table_cell_right),
            Paragraph(f"<font color='{pct_color}'><b>{pct}%</b></font>", table_cell_right),
        ])

    # Summary row
    ward_table_data.append([
        Paragraph("<b>Total / County Average</b>", table_cell_bold),
        Paragraph(f"{len(selected_wards)} Wards", table_cell_bold),
        Paragraph(f"<b>{_fmt_int(total_parcels)}</b>", table_cell_right_bold),
        Paragraph(f"<b>{billed:,.2f}</b>", table_cell_right_bold),
        Paragraph(f"<b>{collected:,.2f}</b>", table_cell_right_bold),
        Paragraph(f"<b>{rate_percent}%</b>", table_cell_right_bold),
    ])

    ward_tbl = Table(ward_table_data, colWidths=w_cols, repeatRows=1, hAlign='LEFT')
    ward_tbl.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 7.5),
        ('BACKGROUND', (0, 0), (-1, 0), band),
        ('LINEBELOW', (0, 0), (-1, 0), 0.7, brand),
        ('LINEBELOW', (0, 1), (-1, -2), 0.25, rule),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, brand),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(ward_tbl)
    story.append(Spacer(1, 3.5 * mm))

    # 6. Arrears Aging & Risk Exposure
    if aging_rows:
        story.append(Paragraph("2. Default & Arrears Aging Breakdown", section_h2))
        aging_cols = [table_w * 0.35, table_w * 0.25, table_w * 0.40]
        aging_table_data = [[
            Paragraph("<b>Aging Category</b>", table_cell_bold),
            Paragraph("<b>Defaulter Count</b>", table_cell_right_bold),
            Paragraph("<b>Overdue Amount (KES)</b>", table_cell_right_bold),
        ]]
        total_aging_amt = Decimal(0)
        total_aging_cnt = 0
        for ar in aging_rows:
            cnt = ar.get('count', 0)
            amt = Decimal(ar.get('amount') or 0)
            total_aging_cnt += cnt
            total_aging_amt += amt
            aging_table_data.append([
                Paragraph(ar.get('bucket', 'Overdue'), table_cell),
                Paragraph(_fmt_int(cnt), table_cell_right),
                Paragraph(f"{amt:,.2f}", table_cell_right),
            ])
        aging_table_data.append([
            Paragraph("<b>Total Delinquent Exposure</b>", table_cell_bold),
            Paragraph(f"<b>{_fmt_int(total_aging_cnt)}</b>", table_cell_right_bold),
            Paragraph(f"<b>{total_aging_amt:,.2f}</b>", table_cell_right_bold),
        ])

        aging_tbl = Table(aging_table_data, colWidths=aging_cols, hAlign='LEFT')
        aging_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), band),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, rule),
            ('LINEBELOW', (0, 1), (-1, -2), 0.25, rule),
            ('LINEBELOW', (0, -1), (-1, -1), 0.7, brand),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(aging_tbl)
        story.append(Spacer(1, 3.5 * mm))

    # 7. Strategic Recommendations (AI Analysis findings)
    story.append(Paragraph("3. AI Strategic Observations & Action Items", section_h2))
    recs = []
    if rate_percent < 50:
        recs.append(
            f"<b>Urgent Compliance Campaign:</b> Overall collection is at {rate_percent}%, below the 75% operational threshold. "
            "Issue digital SMS and demand notices to property owners with debts older than 60 days."
        )
    elif rate_percent < 80:
        recs.append(
            f"<b>Mid-Tier Target Optimization:</b> Revenue collection is moderate ({rate_percent}%). Focus collection enforcement on "
            f"the bottom performing wards ({', '.join([w['ward'] for w in lagging_performers[:2]]) if lagging_performers else 'lagging areas'})."
        )
    else:
        recs.append(
            f"<b>Strong Revenue Health:</b> Collection efficiency is healthy ({rate_percent}%). Maintain settlement momentum and target "
            "the remaining tail of overdue arrears."
        )

    if lagging_performers and top_performers:
        recs.append(
            f"<b>Ward Disparity Intervention:</b> Significant variance observed between top-performing ward "
            f"<b>{top_performers[0]['ward']}</b> and lagging ward <b>{lagging_performers[0]['ward']}</b>. "
            "Deploy field inspection officers to reconcile land registry data and verify billing coordinates."
        )

    recs.append(
        "<b>Enforcement Window:</b> Debts in the 90+ day category require immediate escalation pursuant to the County Rating Act "
        "to prevent statutory limitation lapses."
    )

    for rec in recs:
        story.append(Paragraph(f"• {rec}", body_style))
        story.append(Spacer(1, 1.5 * mm))

    # Build PDF
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    pdf_bytes = out.getvalue()

    # Save to disk
    report_dir = _ensure_report_dir()
    slug_ward = f"_{re.sub(r'[^a-zA-Z0-9]+', '_', ward.lower()).strip('_')}" if ward else ""
    token = uuid.uuid4().hex[:8]
    filename = f"analysis_{year}{slug_ward}_{token}.pdf"
    file_path = report_dir / filename
    file_path.write_bytes(pdf_bytes)

    download_url = f"/api/reports/ai-download/?file={filename}"

    return {
        'status': 'success',
        'title': f"{county_name} Land Rates Executive Analysis ({year})",
        'filename': filename,
        'download_url': download_url,
        'year': year,
        'billed_kes': str(billed),
        'collected_kes': str(collected),
        'outstanding_kes': str(outstanding),
        'collection_rate_pct': float(rate_percent),
        'overdue_count': overdue_count,
        'overdue_amount_kes': str(overdue_amount),
        'summary': (
            f"Executive Analysis PDF generated for {year} ({county_name} County). "
            f"Total Billed: KES {billed:,.2f}, Collected: KES {collected:,.2f} ({rate_percent}%), "
            f"Overdue Exposure: KES {overdue_amount:,.2f}."
        ),
    }


def build_standard_report_pdf(report_type: str = 'collections', year: int = None, ward: str = None, as_of: str = None, from_date: str = None, to_date: str = None, county: str = None, generated_by: str = 'AI Assistant') -> dict:
    """
    Generates statutory reports (collections, arrears, register) as PDF using report_builders.
    Saves to media/ai_reports and returns download URL.
    """
    county_name = (county or '').strip() or None
    kind = (report_type or 'collections').lower().strip()
    today_local = _today()

    if kind in ('collections', 'collection'):
        use_year = int(year or today_local.year)
        report = report_builders.collections_report(use_year, county=county_name)
    elif kind in ('arrears', 'defaulters', 'default'):
        as_of_date = datetime.strptime(as_of, '%Y-%m-%d').date() if as_of else today_local
        report = report_builders.arrears_report(as_of_date, county=county_name)
    elif kind in ('register', 'payments', 'payment_register'):
        start = datetime.strptime(from_date, '%Y-%m-%d').date() if from_date else today_local.replace(day=1)
        end = datetime.strptime(to_date, '%Y-%m-%d').date() if to_date else today_local
        if end < start:
            start, end = end, start
        report = report_builders.register_report(start, end, county=county_name)
    else:
        # Fallback to collections
        use_year = int(year or today_local.year)
        report = report_builders.collections_report(use_year, county=county_name)

    pdf_bytes = report_builders.render_pdf(report, generated_by=generated_by)

    report_dir = _ensure_report_dir()
    token = uuid.uuid4().hex[:8]
    filename = f"{report.slug}_{token}.pdf"
    file_path = report_dir / filename
    file_path.write_bytes(pdf_bytes)

    download_url = f"/api/reports/ai-download/?file={filename}"

    return {
        'status': 'success',
        'title': report.title,
        'subtitle': report.subtitle,
        'filename': filename,
        'download_url': download_url,
        'summary': f"Generated official {report.title} ({report.subtitle}). Available for download.",
    }
