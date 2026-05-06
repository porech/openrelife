"""pytest config: clear sys.argv before openrelife.config parses argv at import."""
import sys

# openrelife.config calls argparse.parse_args() at module import time, which
# would otherwise consume pytest's own CLI args and SystemExit. Clear argv
# before any test imports the package.
sys.argv = [sys.argv[0]]
