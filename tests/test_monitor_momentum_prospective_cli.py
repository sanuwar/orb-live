"""Focused tests for the M55 (Issue #362) monitor CLI: the two genuine
defects found during the initialization run (a self-referential
broker-call-pattern grep, and a master_dates-truncation bug that
silently dropped a real M52 signal from the parity recomputation), plus
the local source/config helpers. The real SEC EDGAR/Alpaca fetch and
the full initialization pipeline are exercised separately (slow, real
network/cache calls, run once as part of the initialization commit,
not repeated here).
"""
from __future__ import annotations

from datetime import date

import scripts.monitor_momentum_prospective as cli
from src.cross_sectional_momentum_research import month_end_signal_dates


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return days


def test_no_broker_call_path_check_passes_on_actual_current_source():
    # Regression test for the self-referential grep bug: the check must
    # not "find" its own denylist literal inside itself.
    result = cli._no_broker_call_path_check()
    assert result["ok"] is True
    assert result["forbidden_pattern_hits"] == []


def test_no_broker_call_path_check_still_catches_a_genuine_hit(tmp_path, monkeypatch):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("import robin_stocks\n", encoding="utf-8")
    monkeypatch.setattr(cli, "SOURCE_FILES_FOR_HASHING", (bad_file,))
    result = cli._no_broker_call_path_check()
    assert result["ok"] is False
    assert any("robin_stocks" in hit for hit in result["forbidden_pattern_hits"])


def test_strategy_enabled_check_reads_false_from_actual_config():
    result = cli._strategy_enabled_check()
    assert result == {"ok": True, "strategy_enabled": False}


def test_source_file_hashes_match_actual_files_on_disk():
    import hashlib
    hashes = cli._source_file_hashes()
    assert set(hashes) == {"src/momentum_prospective_monitor.py", "scripts/monitor_momentum_prospective.py"}
    for rel_path, sha in hashes.items():
        assert sha == hashlib.sha256((cli.ROOT / rel_path).read_bytes()).hexdigest()


def test_month_end_signal_dates_sensitive_to_master_dates_truncation():
    # Regression test for the parity-truncation bug the initialization
    # run actually caught: month_end_signal_dates picks the LAST
    # session of each calendar month PRESENT IN master_dates, not the
    # true calendar month-end. Extending master_dates past a fixed
    # evaluation_end silently drops what would otherwise be
    # evaluation_end's own month-end signal, because the "August
    # month-end" computed from the extended array is now a later date
    # that falls outside [start, evaluation_end] and gets filtered out.
    # This is exactly why the M55 CLI must use a master_dates array
    # truncated identically to M52's own (ALPACA_FETCH_END_EXCLUSIVE),
    # never the CLI's own forward-looking extended array, for the
    # parity recomputation.
    truncated = _business_days(date(2026, 8, 1), 9)  # ends mid-August
    full_month = _business_days(date(2026, 8, 1), 23)  # extends through August's true end
    evaluation_end = truncated[-1]
    start = truncated[0]

    truncated_signals = month_end_signal_dates(truncated, start=start, end=evaluation_end)
    full_signals = month_end_signal_dates(full_month, start=start, end=evaluation_end)

    assert evaluation_end in truncated_signals
    assert evaluation_end not in full_signals
