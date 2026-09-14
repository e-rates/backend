import logging
import uuid
from contextvars import ContextVar

from django.db import transaction

logger = logging.getLogger(__name__)

NO_OBJECT = uuid.UUID(int=0)
_current_request: ContextVar = ContextVar('audit_request', default=None)

CATEGORIES = {
    'auth': 'Logins & accounts',
    'parcel': 'Parcel allocations',
    'payment': 'Payments',
}


class AuditRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


def _client(request):
    if request is None:
        return None, None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR')
    return ip or None, (request.META.get('HTTP_USER_AGENT') or '')[:500]


def mask(identifier: str) -> str:
    value = (identifier or '').strip()
    return f"{'•' * max(len(value) - 3, 0)}{value[-3:]}" if len(value) > 3 else '•••'


def record(action: str, *, obj=None, object_type: str = None, object_id=None, who=None, **details):
    from .models import AuditLog

    request = _current_request.get()
    if who is None and request is not None and getattr(request, 'user', None) and request.user.is_authenticated:
        who = request.user
    ip, agent = _client(request)
    try:
        with transaction.atomic():
            AuditLog.objects.select_for_update().order_by('-audit_id').first()
            AuditLog.objects.create(
                who=who,
                action=action,
                object_type=object_type or (obj._meta.model_name if obj is not None else 'system'),
                object_id=object_id or (obj.pk if obj is not None and isinstance(obj.pk, uuid.UUID) else NO_OBJECT),
                ip_address=ip,
                user_agent=agent,
                details={k: v for k, v in details.items() if v is not None},
            )
    except Exception:
        logger.exception('Failed to write audit log for %s', action)  # never block the audited action


def describe(log) -> str:
    d = log.details or {}
    who = log.who.username if log.who_id else d.get('identifier', 'Someone')
    money = lambda v: f"KES {float(v):,.0f}" if v is not None else ''
    plot = f"plot {d['parcel_ref']}" if d.get('parcel_ref') else 'a plot'
    Plot = 'P' + plot[1:]
    return {
        'auth.login': f"{who} signed in",
        'auth.login_failed': f"Failed sign-in for {d.get('identifier', who)}",
        'auth.login_locked': f"{who} was locked out after repeated failed sign-ins",
        'auth.password_changed': f"{who} changed their password",
        'auth.profile_updated': f"{who} updated {', '.join(d.get('fields', [])) or 'their profile'}",
        'parcel.assigned': f"{Plot} allocated to {d.get('new_owner')}",
        'parcel.transferred': f"{Plot} transferred from {d.get('previous_owner')} to {d.get('new_owner')}",
        'payment.bills_issued': f"{d.get('created', 0)} rate bills issued for {d.get('year')}",
        'payment.prompt_sent': f"M-Pesa prompt for {money(d.get('amount'))} sent for {plot}",
        'payment.completed': f"{money(d.get('amount'))} paid for {plot}" + (f" · {d['receipt']}" if d.get('receipt') else ''),
        'payment.failed': f"Payment for {plot} not completed: {d.get('reason', 'unknown reason')}",
        'payment.amount_mismatch': f"Payment for {plot} flagged: paid {money(d.get('paid'))}, billed {money(d.get('billed'))}",
        'payment.confirmed_manually': f"{money(d.get('amount'))} for {plot} confirmed manually",
        'payment.refunded': f"{money(d.get('amount'))} for {plot} refunded",
    }.get(log.action, log.action.replace('.', ' ').replace('_', ' ').capitalize())
