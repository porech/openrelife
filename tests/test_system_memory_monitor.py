"""Check physical/compressed units, app attribution, and actual swap activity."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "system_memory_monitor", Path(__file__).parent / "manual/monitor_system_memory.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def test_vmstat_uses_native_page_size_and_distinguishes_compressed_storage():
    result = monitor.parse_vmstat('''Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 64.
Pages active: 128.
Pages inactive: 256.
Pages wired down: 512.
File-backed pages: 100.
Pages occupied by compressor: 1024.
Pages stored in compressor: 4096.
Swapins: 10000.
Swapouts: 20000.
''')
    assert result["free_mib"] == 1
    assert result["compressed_physical_mib"] == 16
    assert result["compressed_logical_mib"] == 64


def test_swap_occupancy_is_separate_from_pressure():
    result = monitor.parse_sysctl('''hw.memsize: 34359738368
vm.swapusage: total = 7168.00M  used = 6064.12M  free = 1103.88M  (encrypted)
kern.memorystatus_vm_pressure_level: 1
''')
    assert result["total_mib"] == 32768
    assert result["swap_used_mib"] == 6064.12
    assert result["pressure"] == "normal"


def test_nested_helpers_are_grouped_with_outer_app():
    assert monitor.app_name('/Applications/Comet.app/Contents/Frameworks/Comet Helper.app/Contents/MacOS/Helper') == "Comet"
    assert monitor.app_name('/Users/example/.codex/bin/codex') == "codex"
    assert monitor.app_name('/Users/example/Library/Application Support/OpenReLife/venv/bin/python') == "OpenReLife"


def sample(epoch, incoming, outgoing):
    return dict(epoch=epoch, system=dict(page_size=16384,
                swapins_pages=incoming, swapouts_pages=outgoing))


def test_swap_rate_uses_elapsed_time_and_counter_delta():
    result = monitor.swap_delta(sample(0, 100, 200), sample(300, 164, 13000))
    assert result["swapin_mib"] == 1
    assert result["swapout_mib"] == 200
    assert result["swapout_mib_per_minute"] == 40


def test_counter_reset_is_not_negative_swap():
    assert monitor.swap_delta(sample(0, 100, 200), sample(300, 1, 2)) is None
