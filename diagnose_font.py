import os, sys

print("=== STEP 1: Check fpdf2 version ===")
try:
    import fpdf
    print("fpdf2 version:", getattr(fpdf, "__version__", "unknown"))
    if hasattr(fpdf, "__version__"):
        major, minor, *_ = fpdf.__version__.split(".")
        if int(major) < 2 or (int(major) == 2 and int(minor) < 7):
            print("!! WARNING: fpdf2 < 2.7 may not support set_fallback_fonts() properly. Run: pip install -U fpdf2")
except ImportError:
    print("!! fpdf not installed at all. Run: pip install fpdf2")
    sys.exit(1)

print("\n=== STEP 2: Check font file location ===")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONT_DIR = os.path.join(BASE_DIR, "fonts")
print("Looking in:", BUNDLED_FONT_DIR)
if not os.path.isdir(BUNDLED_FONT_DIR):
    print("!! fonts/ folder does not exist next to this script.")
else:
    files = os.listdir(BUNDLED_FONT_DIR)
    print("Files found in fonts/:", files)
    eth_files = [f for f in files if "ethiop" in f.lower()]
    if not eth_files:
        print("!! No file with 'ethiopic' in the name found in fonts/. Check exact filename/case.")
    else:
        for f in eth_files:
            full = os.path.join(BUNDLED_FONT_DIR, f)
            size = os.path.getsize(full)
            print(f"  -> {f}: {size} bytes", "(SUSPICIOUSLY SMALL - possibly corrupt/HTML error page, not a real font)" if size < 10000 else "(looks OK)")

print("\n=== STEP 3: Try actually loading it into fpdf2 ===")
from fpdf import FPDF
candidate = None
if os.path.isdir(BUNDLED_FONT_DIR):
    for f in os.listdir(BUNDLED_FONT_DIR):
        if "ethiop" in f.lower():
            candidate = os.path.join(BUNDLED_FONT_DIR, f)
            break

if not candidate:
    print("Skipping load test — no candidate file found (see Step 2).")
else:
    try:
        pdf = FPDF()
        pdf.add_font("Ethiopic", fname=candidate)
        print("Font loaded into fpdf2 successfully.")

        pdf.add_page()
        # Need a base font too
        main_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            os.path.join(BUNDLED_FONT_DIR, "DejaVuSans.ttf"),
            os.path.join(BUNDLED_FONT_DIR, "NotoSans-Regular.ttf"),
        ]
        main_font = next((p for p in main_candidates if os.path.exists(p)), None)
        if main_font:
            pdf.add_font("Main", fname=main_font)
            pdf.set_font("Main", size=14)
            pdf.set_fallback_fonts(["Ethiopic"])
            test_text = "\u1230\u1209\u1295\u1275\u1361 \u12a0\u121b\u122d\u129b"  # "selam" / hello-ish Amharic sample
            pdf.multi_cell(0, 10, test_text)
            out = os.path.join(BASE_DIR, "diagnostic_output.pdf")
            pdf.output(out)
            print(f"Test PDF written to: {out}")
            print("Open it and check if you see real Amharic glyphs or boxes.")
        else:
            print("!! Couldn't find a base font to pair with Ethiopic for the test render.")
    except Exception as e:
        print("!! FAILED to load/use the font:", repr(e))

print("\n=== DONE ===")
