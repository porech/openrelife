"""Give every test a disposable application store before configuration imports."""
import sys
import tempfile

# openrelife.config calls argparse.parse_args() at module import time, which
# would otherwise consume pytest's own CLI args and SystemExit. Never let test
# logging, databases, settings, or safety state touch the installed app's store.
_test_storage = tempfile.TemporaryDirectory(prefix="openrelife-tests-")
sys.argv = [sys.argv[0], "--storage-path", _test_storage.name]


def pytest_unconfigure(config):
    _test_storage.cleanup()
