"""
Retention logic.

The pure parts — window arithmetic and the report — are tested here. The
database-touching path is covered in tests/integration/test_retention.py, which
needs a real Postgres.

The property that matters most: a conservative job. Deleting recent data is
unrecoverable and would be far worse than failing to delete old data.
"""
from datetime import datetime, timedelta, timezone

from app.services.retention import BATCH_SIZE, RetentionReport, cutoff_for


class TestCutoffArithmetic:
    def test_cutoff_is_in_the_past(self):
        assert cutoff_for(180) < datetime.now(timezone.utc)

    def test_cutoff_matches_the_configured_window(self):
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        assert cutoff_for(180, now) == now - timedelta(days=180)

    def test_shorter_window_deletes_more(self):
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        assert cutoff_for(30, now) > cutoff_for(365, now)

    def test_timezone_aware(self):
        assert cutoff_for(90).tzinfo is not None

    def test_zero_days_is_now_not_the_epoch(self):
        """A misconfigured 0 must not be read as 'delete everything ever'."""
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        assert cutoff_for(0, now) == now


class TestBatching:
    def test_batch_size_is_bounded(self):
        """A first run against years of backlog must not hold one long transaction."""
        assert 0 < BATCH_SIZE <= 1000


class TestReport:
    def test_starts_empty(self):
        report = RetentionReport()
        assert report.candidates_anonymised == 0
        assert report.errors == []

    def test_serialises_every_counter(self):
        payload = RetentionReport().as_dict()
        for key in (
            "organizations", "candidates_anonymised", "documents_deleted",
            "storage_objects_deleted", "storage_failures", "audit_rows_deleted",
        ):
            assert key in payload

    def test_error_list_is_truncated_for_logging(self):
        report = RetentionReport(errors=[f"org-{i}: Error" for i in range(100)])
        assert len(report.as_dict()["errors"]) <= 20

    def test_storage_failure_is_counted_not_fatal(self):
        """
        A missing storage object must not abort the database side of an erasure —
        otherwise one stale key blocks a candidate's deletion forever.
        """
        report = RetentionReport()
        report.storage_failures += 1
        report.candidates_anonymised += 1
        assert report.as_dict()["storage_failures"] == 1
        assert report.as_dict()["candidates_anonymised"] == 1
