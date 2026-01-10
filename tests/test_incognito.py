#!/usr/bin/env python3
"""
Test script to verify incognito detection.
Run with different browser windows in focus to test.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openrelife.utils import (
    get_active_app_name,
    get_active_window_title,
    get_ax_window_title_osx,
    is_browser_incognito,
    _normalize_text,
    INCOGNITO_END_PATTERNS,
)

def main():
    print("=" * 60)
    print("INCOGNITO DETECTION TEST")
    print("Focus a browser window and run this script")
    print("=" * 60)

    app_name = get_active_app_name()
    window_title = get_active_window_title()  # kCGWindowName

    print(f"\nApp Name: {app_name}")
    print(f"Window Title (kCGWindowName): {window_title}")

    if sys.platform == "darwin":
        ax_title = get_ax_window_title_osx()
        print(f"AXTitle (full): {ax_title}")
        print(f"AXTitle normalized: {_normalize_text(ax_title)}")

    print("\n" + "-" * 60)
    print("DETECTION RESULT:")
    print("-" * 60)

    is_incognito = is_browser_incognito()

    if is_incognito:
        print("✅ INCOGNITO DETECTED!")
    else:
        print("❌ Not in incognito mode (or not a browser)")

    print("\n" + "-" * 60)
    print("Pattern matching debug:")
    print("-" * 60)

    if sys.platform == "darwin":
        ax_title = get_ax_window_title_osx()
        if ax_title:
            title_norm = _normalize_text(ax_title)
            print(f"Normalized title ends with: ...{title_norm[-30:]}")
            print(f"\nChecking patterns:")
            for pattern in INCOGNITO_END_PATTERNS[:6]:  # Show first 6 patterns
                pattern_norm = _normalize_text(pattern)
                matches = title_norm.endswith(pattern_norm)
                symbol = "✅" if matches else "  "
                print(f"  {symbol} '{pattern}' -> {matches}")

if __name__ == "__main__":
    main()
