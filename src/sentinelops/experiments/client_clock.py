"""Observe invalid client UTC bounds without converting or repairing clocks."""
from sentinelops.contracts import utc_time


class ClientClockDiscontinuity(RuntimeError):
    """Original UTC samples do not form a prospective client interval."""


class ClientClockGuard:
    """Latch the failure; raise from the owning load loop, not an orphan task."""
    def __init__(self):
        self.invalid = False

    def observe(self, outcome, started_at):
        start, end = utc_time(outcome['started_at']), utc_time(outcome['completed_at'])
        if not utc_time(started_at) <= start <= end:
            self.invalid = True

    def check(self):
        if self.invalid:
            raise ClientClockDiscontinuity('client UTC interval is outside prospective bounds; original source retained')
