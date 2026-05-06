"""Reproducible PNG fixture generator for Apple Vision integration tests.

Run: python tests/fixtures/apple_vision/generate.py

Generates 3 PNGs in this directory. Re-run to regenerate identically.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent
W, H = 800, 600
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FONT_SIZE = 48
TEXT_POS = (40, 40)


def render(text: str, out_name: str):
    img = Image.new("RGB", (W, H), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()
    if text:
        draw.text(TEXT_POS, text, fill="black", font=font)
    img.save(OUT_DIR / out_name, "PNG", optimize=True)


def main():
    # Note: avoid `è` accent in Italian text to keep file ASCII-stable across font rendering quirks
    render("Ciao mondo, questa e una prova", "italian_simple.png")
    render("Hello world, this is a test", "english_simple.png")
    render("", "blank.png")
    print(f"Generated 3 fixtures in {OUT_DIR}")


if __name__ == "__main__":
    main()
