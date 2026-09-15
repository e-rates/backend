import json
import re
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import rate_reports, report_builders
from .models import User
from .rate_reports import county_q

MAX_ROWS = 40
MAX_TABLE_ROWS = 15
LLM_READ_TIMEOUT = 60
LLM_MAX_TOKENS = 120
MAX_HISTORY_CHARS = 300
PDF_TOOLS = ('generate_analysis_pdf', 'generate_report_pdf')

SYSTEM_PROMPT = (
    'You are the E-Rates assistant, a land rates and revenue helper for Kenyan county officials. '
    'You can summarise collections, list defaulters and overdue plots, look up a plot or an owner, '
    'show payments received, compare rating years, and generate official PDF reports. '
    'The official has already been shown a table with the exact figures. '
    'Reply in 1-3 short plain sentences pointing out what stands out, using the percentages and counts in FACTS. '
    'Never repeat or calculate KES amounts, never invent figures, years or comparisons, and never draw tables. '
    'If there are no FACTS, answer briefly and say what you can help with.'
)


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


def _kes(value):
    return f'KES {Decimal(str(value or 0)):,.2f}'


def _pct(part, whole):
    return round(float(part) / float(whole) * 100, 1) if whole else 0.0


def _scope(county):
    return f' — {re.sub(r"\s+county$", "", county, flags=re.I)} County' if county else ' — all counties'


def _table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + ' --- |' * len(headers)]
    lines += ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in rows[:MAX_TABLE_ROWS]]
    more = len(rows) - MAX_TABLE_ROWS
    return '\n'.join(lines) + (f'\n\n*…and {more} more.*' if more > 0 else '')


def _result(markdown, facts=None):
    return {'markdown': markdown, 'facts': facts or {}}


def _bill_row(bill):
    receipt = bill.processor_ref if bill.status == 'completed' and bill.processor_ref and not bill.processor_ref.startswith('ws_CO_') else None
    deadline = bill.deadline.astimezone(report_builders.NAIROBI).date() if bill.deadline else None
    return (bill.payment_year, _kes(bill.amount), rate_reports.bill_state(bill).replace('_', ' '), deadline or '—', receipt or '—')


def tool_collections_summary(args, county, user):
    year = _year(args)
    totals = rate_reports.collections(year, county=county)
    wards = rate_reports.wards(year, county=county)
    billed = sum((w['outstanding'] + w['collected'] for w in wards), Decimal(0))
    rate = _pct(totals['collected_for_year'], billed)
    rows = [
        (w['ward'].title(), w['sub_county'], w['billed'], w['paid'], w['overdue'], _kes(w['collected']), _kes(w['outstanding']))
        for w in wards
    ]
    markdown = (
        f'### Collections {year}{_scope(county)}\n\n'
        f'- **Collected for {year}:** {_kes(totals["collected_for_year"])}\n'
        f'- **Billed for {year}:** {_kes(billed)}\n'
        f'- **Collected today:** {_kes(totals["collected_today"])}\n'
        f'- **Collection rate:** {rate}%\n\n'
        + (_table(['Ward', 'Sub-county', 'Bills', 'Paid', 'Overdue', 'Collected', 'Outstanding'], rows) if rows else '*No billed plots yet.*')
    )
    bills = sum(w['billed'] for w in wards)
    return _result(markdown, {
        'year': year,
        'collection_rate_percent': rate,
        'wards': len(wards),
        'bills': bills,
        'paid_percent': _pct(sum(w['paid'] for w in wards), bills),
        'overdue_bills': sum(w['overdue'] for w in wards),
        'ward_with_most_overdue': wards[0]['ward'] if wards and wards[0]['overdue'] else None,
    })


def tool_ward_summary(args, county, user):
    year = _year(args)
    wards = rate_reports.wards(year, county=county)
    rows = [(w['ward'].title(), w['sub_county'], w['parcels'], w['paid'], w['unpaid'], w['overdue'], _kes(w['outstanding'])) for w in wards]
    markdown = f'### Wards {year}{_scope(county)}\n\n' + (
        _table(['Ward', 'Sub-county', 'Plots', 'Paid', 'Unpaid', 'Overdue', 'Outstanding'], rows) if rows else '*No wards with allocated plots.*'
    )
    return _result(markdown, {
        'year': year,
        'wards': len(wards),
        'overdue_bills': sum(w['overdue'] for w in wards),
        'ward_with_most_overdue': wards[0]['ward'] if wards and wards[0]['overdue'] else None,
    })


def tool_ward_parcels(args, county, user):
    ward = str(args.get('ward') or '').strip()
    if not ward:
        return _result('Name a ward to list its plots.')
    year = _year(args)
    plots = rate_reports.ward_parcels(ward, year, county=county)
    if not plots:
        return _result(f'No allocated plots found in {ward.title()} ward{_scope(county)}.')
    rows = [
        (p['parcel_ref'], p['owner'], p['status'].replace('_', ' '), _kes(p['amount']) if p['amount'] else '—', p['days_overdue'] or '—')
        for p in plots
    ]
    paid = sum(1 for p in plots if p['status'] == 'paid')
    return _result(
        f'### {ward.title()} ward plots {year}{_scope(county)}\n\n' + _table(['Plot', 'Owner', 'Status', 'Bill', 'Days overdue'], rows),
        {'ward': ward, 'year': year, 'plots': len(plots), 'paid_percent': _pct(paid, len(plots)),
         'overdue_plots': sum(1 for p in plots if p['status'] == 'overdue')},
    )


def tool_defaulters(args, county, user):
    as_of = _date(args.get('as_of'), _today())
    ward = str(args.get('ward') or '').strip().lower()
    report = report_builders.arrears_report(as_of, county=county)
    overdue = [r for r in report.sections[2].rows if not ward or r['ward'].lower() == ward]
    heading = f'### Defaulters as of {as_of}{_scope(county)}' + (f', {ward.title()} ward' if ward else '')
    if not overdue:
        return _result(f'{heading}\n\nNo overdue bills.', {'overdue_bills': 0})
    rows = [(r['plot'], r['ward'].title(), r['owner'], r['year'], _kes(r['amount']), r['days']) for r in overdue]
    markdown = (
        f'{heading}\n\n'
        f'- **Overdue bills:** {len(overdue)}\n'
        f'- **Defaulters:** {len({r["owner"] for r in overdue})}\n'
        f'- **Total arrears:** {_kes(sum(r["amount"] for r in overdue))}\n\n'
        + _table(['Plot', 'Ward', 'Owner', 'Year', 'Amount', 'Days overdue'], rows)
    )
    return _result(markdown, {
        'overdue_bills': len(overdue),
        'defaulters': len({r['owner'] for r in overdue}),
        'oldest_days_overdue': max(r['days'] for r in overdue),
        'bills_over_90_days': sum(1 for r in overdue if r['days'] > 90),
    })


def tool_plot_lookup(args, county, user):
    from .models import Parcel
    ref = str(args.get('plot') or '').strip()
    parcel = Parcel.objects.filter(county_q('county', county), is_deleted=False).filter(
        Q(parcel_ref__iexact=ref) | Q(parcel_ref__iexact=ref.split('/')[-1])
    ).select_related('owner_user').first()
    if not parcel:
        return _result(f'No plot {ref!r} found{_scope(county) if county else ""}.')
    props = parcel.props or {}
    bills = list(parcel.payments.filter(is_deleted=False).order_by('-payment_year'))
    markdown = (
        f'### Plot {parcel.parcel_ref}\n\n'
        + (f'- **Title ref:** {props["REG_SECTIO"]}/{parcel.parcel_ref}\n' if props.get('REG_SECTIO') else '')
        + f'- **Location:** {(parcel.ward or "Unassigned").title()} ward, {parcel.sub_county}, {parcel.county}\n'
        f'- **Area:** {round(parcel.area_m2 or 0):,} m²\n'
        f'- **Land use:** {parcel.land_use or "—"}\n'
        f'- **Owner:** {parcel.owner_user.username if parcel.owner_user else "Unallocated"}\n\n'
        + (_table(['Year', 'Amount', 'Status', 'Deadline', 'Receipt'], [_bill_row(b) for b in bills]) if bills else '*No bills yet.*')
    )
    return _result(markdown, {
        'bills': len(bills),
        'unpaid_bills': sum(1 for b in bills if rate_reports.bill_state(b) in ('unpaid', 'overdue')),
        'allocated': bool(parcel.owner_user),
    })


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
    target = re.sub(r'[^a-z0-9]', '', name.lower())
    if len(target) >= 3:
        for candidate in people.filter(username__icontains=target[:3])[:200]:
            if re.sub(r'[^a-z0-9]', '', candidate.username.lower()) == target:
                return candidate
    return None


def tool_owner_lookup(args, county, user):
    name = str(args.get('owner') or '').strip()
    owner = _find_owner(name)
    plots = list(owner.parcels.filter(county_q('county', county), is_deleted=False)[:MAX_ROWS]) if owner else []
    if not plots:
        return _result(f'No owner {name!r} with plots found{_scope(county) if county else ""}.')
    rows = []
    for plot in plots:
        rows += [(plot.parcel_ref, (plot.ward or 'Unassigned').title(), *_bill_row(b)[:3]) for b in plot.payments.filter(is_deleted=False).order_by('-payment_year')]
    return _result(
        f'### Plots owned by {owner.username}\n\n' + (_table(['Plot', 'Ward', 'Year', 'Amount', 'Status'], rows) if rows else '*No bills yet.*'),
        {'plots': len(plots), 'bills': len(rows), 'overdue_bills': sum(1 for r in rows if r[4] == 'overdue')},
    )


def tool_payments_in_period(args, county, user):
    today = _today()
    start = _date(args.get('from'), today.replace(day=1))
    end = _date(args.get('to'), today)
    start, end = min(start, end), max(start, end)
    report = report_builders.register_report(start, end, county=county)
    payments = report.sections[0].rows
    heading = f'### Payments received {start} to {end}{_scope(county)}'
    if not payments:
        return _result(f'{heading}\n\nNo confirmed payments in this period.', {'payments': 0})
    rows = [(p['paid_at'], p['receipt'], p['plot'] or '—', p['payer'], _kes(p['amount'])) for p in payments]
    without_receipt = sum(1 for p in payments if p['receipt'] == 'Pending reconciliation')
    markdown = (
        f'{heading}\n\n'
        f'- **Payments:** {len(payments)}\n'
        f'- **Total received:** {_kes(sum(p["amount"] for p in payments))}\n\n'
        + _table(['Paid at', 'Receipt', 'Plot', 'Payer', 'Amount'], rows)
    )
    return _result(markdown, {'payments': len(payments), 'without_mpesa_receipt': without_receipt})


def tool_years_summary(args, county, user):
    years = rate_reports.years(county=county)
    if not years:
        return _result(f'### Rating years{_scope(county)}\n\nNo bills have been issued yet.')
    rows = [
        (y['year'], y['bills'], y['paid_bills'], y['unpaid_bills'], _kes(y['billed']), _kes(y['collected']), _kes(y['outstanding']))
        for y in reversed(years)
    ]
    return _result(
        f'### Rating years{_scope(county)}\n\n' + _table(['Year', 'Bills', 'Paid', 'Unpaid', 'Billed', 'Collected', 'Outstanding'], rows),
        {
            'years_with_unpaid_bills': [y['year'] for y in years if y['unpaid_bills']],
            'collection_rate_percent_by_year': {y['year']: _pct(y['collected'], y['billed']) for y in years},
        },
    )


def _pdf_result(result):
    if not result.get('download_url'):
        return _result(result.get('error') or 'The report could not be generated.')
    return _result(f'### {result["title"]}\n\n[{result["title"]}]({result["download_url"]})')


def tool_generate_analysis_pdf(args, county, user):
    from . import ai_reports
    return _pdf_result(ai_reports.build_executive_analysis_pdf(
        year=_year(args), ward=str(args.get('ward') or '').strip() or None, county=county,
        generated_by=getattr(user, 'username', 'AI Assistant'),
    ))


def tool_generate_report_pdf(args, county, user):
    from . import ai_reports
    return _pdf_result(ai_reports.build_standard_report_pdf(
        report_type=str(args.get('report_type') or 'collections').strip(), year=_year(args),
        ward=str(args.get('ward') or '').strip() or None, as_of=args.get('as_of'),
        from_date=args.get('from'), to_date=args.get('to'), county=county,
        generated_by=getattr(user, 'username', 'AI Assistant'),
    ))


TOOLS = {
    'generate_analysis_pdf': tool_generate_analysis_pdf,
    'generate_report_pdf': tool_generate_report_pdf,
    'years_summary': tool_years_summary,
    'collections_summary': tool_collections_summary,
    'ward_summary': tool_ward_summary,
    'ward_parcels': tool_ward_parcels,
    'defaulters': tool_defaulters,
    'plot_lookup': tool_plot_lookup,
    'owner_lookup': tool_owner_lookup,
    'payments_in_period': tool_payments_in_period,
}


def route(question: str):
    q = question.lower()
    year = re.search(r'\b(20\d\d)\b', q)
    base = {'year': int(year.group(1))} if year else {}
    plot = re.search(r'\b(?:plot|parcel)\s+(?:no\.?\s*)?([\w/-]*\d[\w/-]*)', q)
    if plot:
        return [('plot_lookup', {'plot': plot.group(1)})]
    if not year and re.search(r'\b(which|what|all|every|each|any)\s+year|\byears\b|year[- ]on[- ]year|per year', q):
        return [('years_summary', {})]
    owner = re.search(r'\b(?:owner|owned by|belongs? to|who is|about)\s+([a-z][\w.@-]*(?:\s+[a-z][\w.@-]*)?)', q)
    if owner:
        return [('owner_lookup', {'owner': owner.group(1).strip()})]
    ward = re.search(r'\b([a-z][a-z-]+)\s+ward\b|\bward\s+(?:of\s+)?([a-z][a-z-]+)', q)
    ward_name = next((g for g in (ward.groups() if ward else ()) if g and g not in ('each', 'every', 'which', 'the', 'per')), None)

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

    if re.search(r'defaulter|overdue|arrear|unpaid|owe|owing|late', q):
        return [('defaulters', {'ward': ward_name} if ward_name else {})]
    if ward_name:
        return [('ward_parcels', {**base, 'ward': ward_name})]
    if re.search(r'receipt|payments? (made|received)|paid (this|last|in)|register', q):
        return [('payments_in_period', {})]
    if re.search(r'\bwards\b', q) and not re.search(r'collect', q):
        return [('ward_summary', base)]
    if re.search(r'collect|revenue|billed|compliance|summary|overview|reconcil|how much|performance|outstanding', q):
        return [('collections_summary', base)]
    return []


def _history_messages(history):
    turns = [t for t in (history or []) if (t.get('text') or '').strip()][-2:]
    return [
        {'role': 'user' if t.get('role') == 'user' else 'assistant', 'content': t['text'].strip()[-MAX_HISTORY_CHARS:]}
        for t in turns
    ]


def _stream_llm(messages):
    endpoint = settings.LLM_API_URL.rstrip('/') + '/chat/completions'
    payload = {
        'model': settings.LLM_MODEL, 'messages': messages, 'stream': True,
        'max_tokens': LLM_MAX_TOKENS, 'temperature': 0.1,
    }
    try:
        resp = requests.post(endpoint, json=payload, stream=True, timeout=(5, LLM_READ_TIMEOUT))
    except requests.RequestException as exc:
        raise AssistantError('The assistant model is unreachable.') from exc
    with resp:
        if resp.status_code != 200:
            raise AssistantError(f'The assistant model returned {resp.status_code}.')
        try:
            for raw in resp.iter_lines():
                line = raw.decode('utf-8').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    delta = json.loads(data)['choices'][0]['delta'].get('content')
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta
        except requests.RequestException as exc:
            raise AssistantError('The assistant model stopped responding.') from exc


def stream(question: str, history=None, county=None, user=None):
    """Yields {text, sources} for the data tables, then {text} model deltas, or {error}."""
    calls = route(question)
    results = []
    for name, args in calls:
        try:
            results.append((name, args, TOOLS[name](args, county, user)))
        except Exception:
            results.append((name, args, _result(f'Could not load {name.replace("_", " ")}.')))
    if results:
        yield {
            'text': '\n\n'.join(r['markdown'] for _, _, r in results) + '\n\n',
            'sources': [{'tool': name, 'args': args} for name, args, _ in results],
        }
    if calls and all(name in PDF_TOOLS for name, _ in calls):
        return
    if not settings.LLM_API_URL:
        if not results:
            yield {'error': 'The assistant model is not configured on the server.'}
        return

    facts = {name: r['facts'] for name, _, r in results if r['facts']}
    content = f'QUESTION: {question}'
    if facts:
        content = (
            f'FACTS: {json.dumps(facts, default=str, separators=(",", ":"))}\n'
            'The official already sees every KES amount on screen. Comment only on the percentages, counts '
            'and which ward stands out. Write no money amounts.\n'
            f'{content}'
        )
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}, *_history_messages(history), {'role': 'user', 'content': content}]
    shown = json.dumps(facts, default=str) + ''.join(r['markdown'] for _, _, r in results).replace(',', '')
    allowed = {float(n) for n in re.findall(r'\d+(?:\.\d+)?', shown)}
    pending = ''
    said = False
    try:
        for delta in _stream_llm(messages):
            pending += delta
            *sentences, pending = re.split(r'(?<=[.!?])\s+', pending)
            for sentence in sentences:
                if _grounded(sentence, allowed):
                    said = True
                    yield {'text': sentence + ' '}
        if pending.strip() and _grounded(pending, allowed):
            said = True
            yield {'text': pending}
    except AssistantError as exc:
        yield {'error': str(exc)}
        return
    if not said and facts:
        comment = _facts_comment(facts)
        if comment:
            yield {'text': comment}


def _facts_comment(facts):
    merged = {k: v for tool_facts in facts.values() for k, v in tool_facts.items()}
    parts = []
    if isinstance(merged.get('collection_rate_percent'), (int, float)):
        parts.append(f'collection rate is {merged["collection_rate_percent"]}%')
    if merged.get('overdue_bills'):
        n = merged['overdue_bills']
        parts.append(f'{n} overdue bill{"s" if n != 1 else ""}')
    if merged.get('ward_with_most_overdue'):
        parts.append(f'{merged["ward_with_most_overdue"].title()} ward has the most overdue bills')
    if merged.get('oldest_days_overdue'):
        parts.append(f'the oldest has been unpaid for {merged["oldest_days_overdue"]} days')
    if merged.get('years_with_unpaid_bills'):
        parts.append('unpaid bills remain from ' + ', '.join(str(y) for y in merged['years_with_unpaid_bills']))
    if not parts:
        return ''
    text = '; '.join(parts)
    return text[0].upper() + text[1:] + '.'


def _grounded(sentence, allowed):
    """Small models misquote figures, so a sentence survives only if every number in it was shown to the official."""
    return all(float(n.replace(',', '')) in allowed for n in re.findall(r'\d[\d,]*(?:\.\d+)?', sentence) if n.strip(','))
