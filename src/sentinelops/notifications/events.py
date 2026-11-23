"""Turn proposal state and warning episodes into notifications with stable keys."""
from __future__ import annotations

from datetime import timedelta, timezone

from sentinelops.contracts import utc_time
from sentinelops.notifications import Notification

LOCAL = timezone(timedelta(hours=7))
OUTCOMES = {'recovered': 'Đã hồi phục', 'escalated': 'Cần kỹ sư xử lý', 'rejected': 'Đã từ chối',
            'obsolete': 'Đã đóng do tình trạng thay đổi'}


def _when(value):
    moment = utc_time(value)
    return (f"{moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"({moment.astimezone(LOCAL).strftime('%H:%M:%S')} +07:00)")


def _subject(text):
    return text if len(text) <= 100 else text[:99] + '…'


def _outcome_phase(view):
    final = view.get('final')
    if final and final.get('phase') in ('recovered', 'escalated', 'blocked'):
        return 'recovered' if final['phase'] == 'recovered' else 'escalated'
    action = view.get('memory_action')  # present only on OOM proposal views
    if action:
        phase = action.get('phase')
        if phase in ('recovered', 'escalated', 'rejected', 'obsolete'):
            return phase
        if phase in ('blocked', 'expired'):
            return 'escalated'
    outcome = view.get('outcome')
    if outcome:
        if outcome.get('phase') == 'obsolete':
            return 'obsolete'
        return 'rejected' if outcome.get('reason') == 'approval-rejected' else 'escalated'
    return None


def proposal_notifications(views, ui_url):
    values = []
    for view in views:
        proposal = view['proposal']
        pid, incident = proposal['proposal_id'], proposal['incident_id']
        values.append(Notification('proposal_pending', 'proposal:' + pid,
            _subject(f"[SentinelOps] Cần duyệt: {proposal['runbook_id']} cho {incident}"),
            f"Sự cố {incident} có đề xuất xử lý đang chờ người trực duyệt.\n"
            f"Runbook: {proposal['runbook_id']}\n"
            f"Tạo lúc: {_when(proposal['created_at'])}\n"
            "Đề xuất chờ tới khi có quyết định, không có hạn giờ.\n"
            f"Xem bằng chứng và duyệt tại: {ui_url}\n"))
        phase = _outcome_phase(view)
        if phase:
            reason = (view.get('outcome') or {}).get('reason') or (view.get('memory_action') or {}).get('reason')
            values.append(Notification('outcome', f'outcome:{pid}:{phase}',
                _subject(f"[SentinelOps] {OUTCOMES[phase]}: {incident}"),
                f"Kết quả xử lý sự cố {incident}: {OUTCOMES[phase]}.\n"
                + (f"Lý do: {reason}\n" if reason and phase != 'recovered' else '')
                + f"Chi tiết: {ui_url}\n"))
    return values


def warning_notifications(active, dispatcher, ui_url):
    """`active` maps service -> episode start (ISO) or None when no episode is open."""
    values = []
    for service, started in sorted(active.items()):
        if started:
            values.append(Notification('early_warning', f'warning:{service}:{started}',
                _subject(f"[SentinelOps] Cảnh báo sớm: {service} có nguy cơ vi phạm SLO"),
                f"Dự báo: {service} có nguy cơ vi phạm SLO trong khoảng 5 phút tới.\n"
                "Đây là dự báo, chưa phải sự cố. Chưa có hành động nào được thực hiện.\n"
                f"Bắt đầu cảnh báo: {_when(started)}\n"
                f"Theo dõi tại: {ui_url}\n",
                (('service', service), ('started_at', started))))
            continue
        for record in dispatcher.records('early_warning'):
            if record.get('service') != service:
                continue
            key = f"warning-cleared:{service}:{record['started_at']}"
            if dispatcher.sent(key):
                continue
            values.append(Notification('warning_cleared', key,
                _subject(f"[SentinelOps] Hết nguy cơ: {service}"),
                f"Cảnh báo sớm cho {service} (bắt đầu {_when(record['started_at'])}) đã kết thúc.\n"
                f"Theo dõi tại: {ui_url}\n"))
    return values
