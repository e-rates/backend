"""Liveness and readiness for the container healthcheck and any load balancer in front."""
from django.db import connection
from django.http import JsonResponse


def health(request):
    """Readiness: the process is up AND the database answers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - report any failure to the probe
        return JsonResponse({'status': 'degraded', 'database': str(exc)[:200]}, status=503)
    return JsonResponse({'status': 'ok', 'database': 'ok'})
