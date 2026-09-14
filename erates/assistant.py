import json
import re
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import rate_reports, report_builders
from .audit import mask
from .models import Parcel, User

MAX_ROWS = 40
MAX_CONTEXT_CHARS = 14_000
LLM_READ_TIMEOUT = 200  # a cold Ollama model load on Colab takes ~100s; warm calls are ~2s
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 2_000
ANSWER_KEYS = ('response', 'answer', 'result', 'text', 'generated_text', 'output')


class AssistantError(Exception):
    pass


def _today():
    return timezone.now().astimezone(report_builders.NAIROBI).date()


def _year(args):
    try:
        return int(args.get('year') or _today().year)
    except (TypeError, ValueError):
        return _today().year


def _date(value, default):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date() if value else default
    except ValueError:
        return default


def _bill_row(bill):
    return {
        'year': bill.payment_year,
        'amount_kes': bill.amount,
        'status': rate_reports.bill_state(bill),
        'deadline': bill.deadline.astimezone(report_builders.NAIROBI).date() if bill.deadline else None,
        'receipt': bill.processor_ref if bill.status == 'completed' and bill.processor_ref and not bill.processor_ref.startswith('ws_CO_') else None,
    }


def tool_collections_summary(args):
    year = _year(args)
    totals = rate_reports.collections(year)
    wards = rate_reports.wards(year)
    billed = sum((Decimal(w['outstanding']) + Decimal(w['collected']) for w in wards), Decimal(0))
    return {
        'year': year,
        'collected_for_year_kes': totals['collected_for_year'],
        'collected_all_time_kes': totals['total_collected'],
        'collected_today_kes': totals['collected_today'],
        'billed_for_year_kes': billed,
        'collection_rate_percent': round(Decimal(totals['collected_for_year']) / billed * 100, 1) if billed else 0,
        'wards': [{k: w[k] for k in ('ward', 'sub_county', 'billed', 'paid', 'unpaid', 'overdue', 'collected', 'outstanding')} for w in wards],
    }


def tool_ward_summary(args):
    year = _year(args)
    return {'year': year, 'wards': rate_reports.wards(year)}


def tool_ward_parcels(args):
    ward = str(args.get('ward') or '').strip()
    if not ward:
        return {'error': 'ward is required'}
    year = _year(args)
    rows = rate_reports.ward_parcels(ward, year)
    for row in rows:
        row['owner_phone'] = mask(row['owner_phone'] or '') if row.get('owner_phone') else None
        row.pop('owner_email', None)
        row.pop('parcel_id', None)
    return {'ward': ward, 'year': year, 'total_plots': len(rows), 'plots': rows[:MAX_ROWS], 'truncated': len(rows) > MAX_ROWS}


def tool_defaulters(args):
    as_of = _date(args.get('as_of'), _today())
    report = report_builders.arrears_report(as_of)
    ward = str(args.get('ward') or '').strip().lower()
    rows = [r for r in report.sections[2].rows if not ward or r['ward'].lower() == ward]
    return {
        'as_of': as_of,
        'ward_filter': ward or None,
        'summary': {label: value for label, value, _ in report.summary},
        'by_age': report.sections[0].rows,
        'by_ward': report.sections[1].rows,
        'defaulters': [
            {k: r[k] for k in ('ward', 'plot', 'title_ref', 'owner', 'year', 'amount', 'deadline', 'days', 'bucket')}
            for r in rows[:MAX_ROWS]
        ],
        'truncated': len(rows) > MAX_ROWS,
    }


def tool_plot_lookup(args):
    ref = str(args.get('plot') or '').strip()
    parcel = Parcel.objects.filter(is_deleted=False).filter(
        Q(parcel_ref__iexact=ref) | Q(parcel_ref__iexact=ref.split('/')[-1])
    ).select_related('owner_user').first()
    if not parcel:
        return {'error': f'No plot {ref!r} found'}
    props = parcel.props or {}
    return {
        'plot': parcel.parcel_ref,
        'title_ref': f"{props['REG_SECTIO']}/{parcel.parcel_ref}" if props.get('REG_SECTIO') else None,
        'county': parcel.county, 'sub_county': parcel.sub_county, 'ward': parcel.ward,
        'area_m2': round(parcel.area_m2 or 0), 'land_use': parcel.land_use, 'status': parcel.status,
        'owner': parcel.owner_user.username if parcel.owner_user else None,
        'bills': [_bill_row(b) for b in parcel.payments.filter(is_deleted=False).order_by('-payment_year')],
    }


def _find_owner(name: str):
    """Owners get asked for by username, by display name, or by phone."""
    if not name:
        return None
    people = User.objects.filter(is_deleted=False)
    user = people.filter(username__iexact=name).first()
    if user:
        return user
    if any(ch.isdigit() for ch in name):
        user = User.find_by_phone(name)
        if user:
            return user
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    if slug:
        user = people.filter(username__iexact=slug).first() or people.filter(username__icontains=slug).first()
        if user:
            return user
    parts = [p for p in re.split(r'\s+', name.strip()) if p]
    if len(parts) > 1:
        matches = people.filter(username__icontains=parts[0]).filter(username__icontains=parts[-1])
        if matches.count() == 1:
            return matches.first()
    # The model often drops the spaces ("JohnDoe"), so compare with separators stripped.
    target = re.sub(r'[^a-z0-9]', '', name.lower())
    if len(target) >= 3:
        for candidate in people.filter(username__icontains=target[:3])[:200]:
            if re.sub(r'[^a-z0-9]', '', candidate.username.lower()) == target:
                return candidate
    return None


def tool_owner_lookup(args):
    name = str(args.get('owner') or '').strip()
    user = _find_owner(name)
    if not user:
        return {'error': f'No owner {name!r} found'}
    parcels = user.parcels.filter(is_deleted=False)
    return {
        'owner': user.username,
        'phone': mask(user.phone) if user.phone else None,
        'plots': [
            {'plot': p.parcel_ref, 'ward': p.ward, 'bills': [_bill_row(b) for b in p.payments.filter(is_deleted=False).order_by('-payment_year')]}
            for p in parcels[:MAX_ROWS]
        ],
    }


def tool_payments_in_period(args):
    today = _today()
    start = _date(args.get('from'), today.replace(day=1))
    end = _date(args.get('to'), today)
    report = report_builders.register_report(min(start, end), max(start, end))
    rows = report.sections[0].rows
    return {
        'from': start, 'to': end,
        'summary': {label: value for label, value, _ in report.summary},
        'payments': [{k: r[k] for k in ('paid_at', 'receipt', 'plot', 'ward', 'payer', 'year', 'amount')} for r in rows[:MAX_ROWS]],
        'truncated': len(rows) > MAX_ROWS,
    }


def tool_years_summary(args):
    rows = rate_reports.years()
    unpaid = [r['year'] for r in rows if r['unpaid_bills']]
    return {
        'years': rows,
        'years_with_unpaid_bills': unpaid,
        'fully_paid_years': [r['year'] for r in rows if not r['unpaid_bills']],
        'note': 'Every rating year that has bills. Use this for questions spanning more than one year.',
    }


def tool_generate_analysis_pdf(args, user=None):
    from . import ai_reports
    year = _year(args)
    ward = str(args.get('ward') or '').strip() or None
    county = getattr(user, 'county', None) if user else None
    generated_by = getattr(user, 'username', 'AI Assistant') if user else 'AI Assistant'
    return ai_reports.build_executive_analysis_pdf(year=year, ward=ward, county=county, generated_by=generated_by)


def tool_generate_report_pdf(args, user=None):
    from . import ai_reports
    report_type = str(args.get('report_type') or 'collections').strip()
    year = _year(args)
    ward = str(args.get('ward') or '').strip() or None
    as_of = args.get('as_of')
    from_date = args.get('from') or args.get('from_date')
    to_date = args.get('to') or args.get('to_date')
    county = getattr(user, 'county', None) if user else None
    generated_by = getattr(user, 'username', 'AI Assistant') if user else 'AI Assistant'
    return ai_reports.build_standard_report_pdf(
        report_type=report_type, year=year, ward=ward, as_of=as_of,
        from_date=from_date, to_date=to_date, county=county, generated_by=generated_by
    )


TOOLS = {
    'generate_analysis_pdf': (tool_generate_analysis_pdf, 'Generate and export an executive AI analysis PDF report with KPI scorecards, ward compliance matrix, arrears risk aging, and recommendations. args: year, ward'),
    'generate_report_pdf': (tool_generate_report_pdf, 'Generate and export an official PDF report for statutory compliance. args: report_type (collections, arrears, or register), year, ward, as_of, from, to'),
    'years_summary': (tool_years_summary, 'Every rating year with bills: billed, collected, outstanding and which years still have unpaid bills. No args. Use for "which years", "all years", or any question not about a single year'),
    'collections_summary': (tool_collections_summary, 'Money billed and collected for a rating year, overall and per ward. args: year'),
    'ward_summary': (tool_ward_summary, 'Per ward: plots, paid, unpaid, overdue counts and amounts. args: year'),
    'ward_parcels': (tool_ward_parcels, 'Every allocated plot in one ward with owner and bill status. args: ward (required), year'),
    'defaulters': (tool_defaulters, 'Overdue unpaid bills with aging buckets, optionally for one ward. args: as_of (YYYY-MM-DD), ward'),
    'plot_lookup': (tool_plot_lookup, 'One plot: location, owner, every bill and receipt. args: plot (plot number or title ref)'),
    'owner_lookup': (tool_owner_lookup, 'One owner by username: their plots and bills. args: owner'),
    'payments_in_period': (tool_payments_in_period, 'Confirmed payments between two dates with receipts. args: from, to (YYYY-MM-DD)'),
}


def _call_llm(prompt: str) -> str:
    url = settings.LLM_API_URL
    if not url:
        raise AssistantError('LLM_API_URL is not configured on the server')
    url = url.rstrip('/')
    openai_style = url.endswith('/v1')
    if openai_style:
        endpoint = f'{url}/chat/completions'
        payload = {
            'model': settings.LLM_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'stream': False,
        }
    else:
        endpoint = f'{url}/analyze'
        payload = {'text': prompt}
    try:
        resp = requests.post(endpoint, json=payload, timeout=(5, LLM_READ_TIMEOUT))
    except requests.RequestException as exc:
        raise AssistantError(f'Could not reach the language model: {exc}') from exc
    if resp.status_code != 200:
        if resp.status_code in (502, 503, 504, 521, 522, 523, 524, 530):
            # Cloudflare/ngrok speak these when the notebook tunnel has dropped.
            raise AssistantError(
                'The assistant is unreachable — the model notebook or its tunnel is down '
                f'(upstream {resp.status_code}).'
            )
        if resp.status_code in (401, 403):
            raise AssistantError(f'The model server refused the request ({resp.status_code}).')
        if resp.status_code == 404:
            raise AssistantError(
                f'The model server has no "{settings.LLM_MODEL}" — check the name against `ollama list`.'
            )
        raise AssistantError(f'Language model returned {resp.status_code}')
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()
    if isinstance(body, str):
        return body.strip()
    if openai_style:
        try:
            return body['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AssistantError('Language model reply had no text')
    for key in ANSWER_KEYS:
        if isinstance(body.get(key), str):
            return body[key].strip()
    raise AssistantError('Language model reply had no text')


def _plan_prompt(question: str) -> str:
    tools = '\n'.join(f'- {name}: {desc}' for name, (_, desc) in TOOLS.items())
    return (
        f'You route questions for the E-Rates land-rates system of {settings.COUNTY_NAME}. Today is {_today()}.\n'
        f'Available read-only data tools:\n{tools}\n\n'
        'Pick at most 3 tools needed to answer the question. Reply with JSON only, no prose, exactly like:\n'
        '{"tools": [{"name": "ward_summary", "args": {"year": 2026}}]}\n\n'
        f'Question: {question}'
    )


def parse_plan(text: str):
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        return []
    try:
        plan = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    calls = []
    for item in (plan.get('tools') or [])[:3] if isinstance(plan, dict) else []:
        if isinstance(item, dict) and item.get('name') in TOOLS:
            args = item.get('args') if isinstance(item.get('args'), dict) else {}
            calls.append((item['name'], args))
    return calls


def fallback_plan(question: str):
    q = question.lower()
    year = re.search(r'\b(20\d\d)\b', q)
    base = {'year': int(year.group(1))} if year else {}
    plot = re.search(r'\b(?:plot|parcel)\s+(?:no\.?\s*)?([\w/-]*\d[\w/-]*)', q)
    if plot:
        return [('plot_lookup', {'plot': plot.group(1)})]
    # "which years", "every year", "all years" — anything spanning more than one year.
    if not year and re.search(r'\b(which|what|all|every|each|any)\s+year|\byears\b|year[- ]on[- ]year|per year', q):
        return [('years_summary', {})]
    owner = re.search(r'\b(?:owner|owned by|belongs? to|who is|about)\s+([a-z][\w.@-]*(?:\s+[a-z][\w.@-]*)?)', q)
    if owner:
        return [('owner_lookup', {'owner': owner.group(1).strip()})]
    ward = re.search(r'\b([a-z][a-z-]+)\s+ward\b|\bward\s+(?:of\s+)?([a-z][a-z-]+)', q)
    ward_name = next((g for g in (ward.groups() if ward else ()) if g and g not in ('each', 'every', 'which', 'the', 'per')), None)

    # Route requests for PDF generation or executive analysis reports
    is_pdf = bool(re.search(r'\b(pdf|download|export|print|generate)\b', q))
    is_analysis = bool(re.search(r'\b(analysis|analytics|intelligence|executive|briefing|review)\b', q))
    if is_pdf and is_analysis:
        return [('generate_analysis_pdf', {**base, **({'ward': ward_name} if ward_name else {})})]
    if is_pdf and re.search(r'defaulter|overdue|arrear', q):
        return [('generate_report_pdf', {'report_type': 'arrears', **base, **({'ward': ward_name} if ward_name else {})})]
    if is_pdf and re.search(r'receipt|payments?|register', q):
        return [('generate_report_pdf', {'report_type': 'register', **base})]
    if is_pdf and re.search(r'report|collections?|revenue', q):
        return [('generate_report_pdf', {'report_type': 'collections', **base})]

    if re.search(r'defaulter|overdue|arrear|owe|late', q):
        return [('defaulters', {'ward': ward_name} if ward_name else {})]
    if ward_name:
        return [('ward_parcels', {**base, 'ward': ward_name})]
    if re.search(r'receipt|payments? (made|received)|paid (this|last|in)|register', q):
        return [('payments_in_period', {})]
    return [('collections_summary', base)]


def _history_block(history) -> str:
    """Recent turns, oldest first, trimmed to fit MAX_HISTORY_CHARS."""
    if not history:
        return ''
    lines = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        role = 'User' if turn.get('role') == 'user' else 'Assistant'
        text = (turn.get('text') or '').strip()
        if text:
            lines.append(f'{role}: {text}')
    if not lines:
        return ''
    block = '\n'.join(lines)
    if len(block) > MAX_HISTORY_CHARS:
        block = '…(earlier turns dropped)\n' + block[-MAX_HISTORY_CHARS:]
    return f'EARLIER IN THIS CONVERSATION:\n{block}\n\n'


def answer(question: str, history=None, user=None) -> dict:
    import inspect

    question = question.strip()
    if not question:
        raise AssistantError('Ask a question')
    # Planning stays stateless: feeding it history makes it re-pick stale tools.
    calls = parse_plan(_call_llm(_plan_prompt(question)))
    used_fallback = not calls
    if used_fallback:
        calls = fallback_plan(question)

    results = []
    for name, args in calls:
        try:
            tool_fn = TOOLS[name][0]
            sig = inspect.signature(tool_fn)
            if 'user' in sig.parameters:
                data = tool_fn(args, user=user)
            else:
                data = tool_fn(args)
        except Exception as exc:
            data = {'error': f'{name} failed: {exc}'}
        results.append({'tool': name, 'args': args, 'data': data})

    context = json.dumps(results, default=str, separators=(',', ':'))
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + '…(truncated)'
    reply = _call_llm(
        f'You are the E-Rates assistant for {settings.COUNTY_NAME}. Today is {_today()}.\n'
        'EVERY number below is an exact figure in Kenyan shillings (KES). They are NOT in thousands, '
        'millions or any other unit. Quote each amount exactly as it appears — do not rescale it, round it, '
        'approximate it, or add a note about what unit it might be in. A total of 1.00 means one shilling. '
        'Never state a total that is not present in the data.\n'
        'When a PDF report or analysis has been generated in the data (status: "success"), highlight the key findings '
        'and always include a clear markdown download link with the title and download_url from the data, '
        'for example: [Download PDF: <title>](<download_url>).\n'
        'Answer the question using ONLY the data below. Always reply in full sentences — never a single word. '
        'If the data does not answer the question, say so in a sentence and name what you would need instead. '
        'Be concise; use a short list or GitHub-flavoured markdown table when comparing several items. '
        'Never invent plots, people or figures.\n\n'
        f'{_history_block(history)}'
        f'DATA: {context}\n\nQUESTION: {question}'
    )
    return {
        'answer': reply,
        'sources': [{'tool': r['tool'], 'args': r['args']} for r in results],
        'used_fallback': used_fallback,
    }
