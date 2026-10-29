"""Verify that trend reports cannot mistake spikes, restarts, or sleep for stability."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "memory_monitor", Path(__file__).parent / "manual/monitor_macos_memory.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def series(count=121, memory=lambda minute: 1500, pid=100, start=1, offset=0):
    return [dict(epoch=offset + minute * 60, backend=dict(
        pid=pid, start=start, footprint_mib=memory(minute))) for minute in range(count)]


def test_stable_memory():
    result = monitor.analyze(series(), 60)
    assert result["assessment"] == "no_sustained_growth_observed"
    assert result["alerts"] == []


def test_one_peak_is_not_sustained_growth():
    result = monitor.analyze(series(memory=lambda minute: 8000 if minute == 120 else 1500), 60)
    assert result["peak_sampled_mib"] == 8000
    assert result["alerts"] == []


def test_slow_cumulative_growth_is_detected():
    result = monitor.analyze(series(memory=lambda minute: 1500 + minute * 10), 60)
    assert result["assessment"] == "growth_requires_review"
    assert "sustained_growth" in result["alerts"]


def test_elevated_memory_is_reported_before_one_hour():
    result = monitor.analyze(series(count=20, memory=lambda minute: 4500), 60)
    assert result["assessment"] == "elevated_memory_requires_review"
    assert "high_sustained_footprint" in result["alerts"]


def test_pid_reuse_does_not_conceal_restart():
    samples = series() + series(count=3, start=2, offset=121 * 60)
    result = monitor.analyze(samples, 60)
    assert result["assessment"] == "insufficient_observation"
    assert result["segment_seconds"] == 120


def test_absent_backend_is_not_stability():
    result = monitor.analyze(series() + [dict(epoch=121 * 60, backend=None)], 60)
    assert result["assessment"] == "backend_not_observed"


def test_sleep_gaps_limit_the_conclusion():
    samples = series(count=20) + series(count=20, offset=8 * 3600)
    result = monitor.analyze(samples, 60)
    assert result["assessment"] == "incomplete_coverage"
    assert result["coverage"] < 0.1
