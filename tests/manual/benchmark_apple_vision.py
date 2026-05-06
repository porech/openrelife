"""Manual benchmark: side-by-side Vision vs doctr on real captured frames.

Run: python tests/manual/benchmark_apple_vision.py [N]
where N is the number of recent frames to test (default 3).

Prints per-phase timing and an output sample for visual quality comparison.
Not run by pytest. Kept in-tree as a regression-check tool.
"""
import os
import sys
import time

os.environ["TOKENIZERS_PARALLELISM"] = "false"


def main(n: int = 3):
    appdata = os.path.expanduser("~/Library/Application Support/OpenReLife")
    screens = os.path.join(appdata, "screenshots")
    files = sorted(
        [f for f in os.listdir(screens) if f.endswith(".webp")],
        key=lambda f: os.path.getmtime(os.path.join(screens, f)),
    )[-n:]
    if not files:
        print(f"No .webp captures found in {screens}. Capture some frames first.")
        sys.exit(1)
    print(f"Benchmarking {len(files)} frames", flush=True)

    from PIL import Image
    import numpy as np
    from openrelife import ocr, apple_vision_ocr

    for fname in files:
        img = Image.open(os.path.join(screens, fname)).convert("RGB")
        w, h = img.size
        if h > 1080:
            scale = 1080 / h
            img = img.resize((int(w * scale), 1080), Image.LANCZOS)
        arr = np.array(img)
        print(f"\n=== {fname} ({arr.shape}) ===")

        if apple_vision_ocr.is_apple_vision_available():
            t = time.perf_counter()
            v_text, v_words = apple_vision_ocr.extract_text_with_vision(arr)
            v_time = time.perf_counter() - t
            print(f"  Vision: {v_time:.2f}s, {len(v_words)} words, sample: {v_text[:80]!r}")
        else:
            print("  Vision: not available on this platform")

        t = time.perf_counter()
        d_text, d_words = ocr._extract_with_doctr(arr)
        d_time = time.perf_counter() - t
        print(f"  doctr:  {d_time:.2f}s, {len(d_words)} words, sample: {d_text[:80]!r}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(n)
