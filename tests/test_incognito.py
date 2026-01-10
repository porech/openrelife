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
    _get_all_visible_browser_windows_osx,
    _is_incognito_title,
    INCOGNITO_END_PATTERNS,
)

def main():
    print("=" * 60)
    print("INCOGNITO DETECTION TEST")
    print("Now checks ALL visible browser windows, not just focused!")
    print("=" * 60)

    print("\n" + "-" * 60)
    print("FOCUSED WINDOW INFO:")
    print("-" * 60)
    app_name = get_active_app_name()
    window_title = get_active_window_title()
    print(f"  App: {app_name}")
    print(f"  Title: {window_title}")

    if sys.platform == "darwin":
        print("\n" + "-" * 60)
        print("ALL VISIBLE BROWSER WINDOWS:")
        print("-" * 60)
        browser_windows = _get_all_visible_browser_windows_osx()
        if browser_windows:
            for i, (app, title) in enumerate(browser_windows, 1):
                is_incog = _is_incognito_title(title)
                symbol = "🔒" if is_incog else "  "
                print(f"  {symbol} [{app}] {title}")
        else:
            print("  (no browser windows found)")

    print("\n" + "-" * 60)
    print("DETECTION RESULT:")
    print("-" * 60)

    is_incognito = is_browser_incognito()

    if is_incognito:
        print("🔒 INCOGNITO DETECTED - Recording will be skipped!")
    else:
        print("✅ No incognito windows visible - Recording allowed")

if __name__ == "__main__":
    main()
