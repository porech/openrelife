#!/usr/bin/env python3
"""
Test script to inspect window attributes on macOS.
Run with a Chrome/Safari window in foreground (normal and incognito) to compare.

Usage:
1. Open a normal Chrome window, focus it, run: python test_window_attrs.py
2. Open an incognito Chrome window, focus it, run: python test_window_attrs.py
3. Compare the outputs
"""

import sys

if sys.platform != "darwin":
    print("This script only works on macOS")
    sys.exit(1)

from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowListOptionAll,
)
from AppKit import NSWorkspace
import ApplicationServices as AppServices

def get_active_app():
    """Get the currently active application name."""
    active_app = NSWorkspace.sharedWorkspace().activeApplication()
    return active_app.get("NSApplicationName", "Unknown")

def get_all_window_properties():
    """Get all properties for all windows of the active app."""
    active_app = get_active_app()
    print(f"\n{'='*60}")
    print(f"Active Application: {active_app}")
    print(f"{'='*60}\n")

    # Get all windows
    window_list = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID
    )

    windows_found = 0
    for window in window_list:
        owner = window.get("kCGWindowOwnerName", "")
        if owner == active_app:
            windows_found += 1
            print(f"\n--- Window {windows_found} ---")

            # Print ALL properties
            for key, value in sorted(window.items()):
                print(f"  {key}: {value}")

    if windows_found == 0:
        print(f"No windows found for {active_app}")

    return windows_found

def try_accessibility_api():
    """Try to get more info via Accessibility API."""
    print(f"\n{'='*60}")
    print("Accessibility API (AXUIElement) attributes:")
    print(f"{'='*60}\n")

    try:
        # Get the frontmost application
        workspace = NSWorkspace.sharedWorkspace()
        active_app = workspace.frontmostApplication()

        if active_app is None:
            print("No frontmost application found")
            return

        pid = active_app.processIdentifier()
        print(f"PID: {pid}")
        print(f"Bundle ID: {active_app.bundleIdentifier()}")

        # Create AXUIElement for the application
        app_ref = AppServices.AXUIElementCreateApplication(pid)

        # Try to get the focused window
        err, focused_window = AppServices.AXUIElementCopyAttributeValue(
            app_ref, "AXFocusedWindow", None
        )

        if err == 0 and focused_window:
            print("\nFocused Window Attributes:")

            # Get all attribute names
            err, attr_names = AppServices.AXUIElementCopyAttributeNames(
                focused_window, None
            )

            if err == 0 and attr_names:
                for attr_name in sorted(attr_names):
                    err, value = AppServices.AXUIElementCopyAttributeValue(
                        focused_window, attr_name, None
                    )
                    if err == 0:
                        # Truncate long values
                        value_str = str(value)
                        if len(value_str) > 100:
                            value_str = value_str[:100] + "..."
                        print(f"  {attr_name}: {value_str}")
                    else:
                        print(f"  {attr_name}: <error reading>")
        else:
            print(f"Could not get focused window (error: {err})")

            # Try AXWindows instead
            err, windows = AppServices.AXUIElementCopyAttributeValue(
                app_ref, "AXWindows", None
            )
            if err == 0 and windows and len(windows) > 0:
                print(f"\nFound {len(windows)} windows via AXWindows, showing first:")
                first_window = windows[0]

                err, attr_names = AppServices.AXUIElementCopyAttributeNames(
                    first_window, None
                )
                if err == 0 and attr_names:
                    for attr_name in sorted(attr_names):
                        err, value = AppServices.AXUIElementCopyAttributeValue(
                            first_window, attr_name, None
                        )
                        if err == 0:
                            value_str = str(value)
                            if len(value_str) > 100:
                                value_str = value_str[:100] + "..."
                            print(f"  {attr_name}: {value_str}")

    except Exception as e:
        print(f"Error accessing Accessibility API: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("\n" + "="*60)
    print("WINDOW ATTRIBUTES INSPECTOR")
    print("Focus a browser window (normal or incognito) before running")
    print("="*60)

    get_all_window_properties()
    try_accessibility_api()

    print("\n" + "="*60)
    print("TIP: Run this twice - once with normal window, once with")
    print("incognito - and diff the outputs to find differences")
    print("="*60 + "\n")
