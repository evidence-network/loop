"""Contrast and label size on the stats page, in both colour schemes. Standard library only, no network.

The page defines its colours once, as CSS variables on :root, and again for
prefers-color-scheme: dark. Every rule that colours text is read from
site/template.html:
  - `color: var(--a)`, against the rule's own `background: var(--b)` or the page's --bg;
  - `fill: var(--a)` on an SVG text selector, against --bg;
  - `background: var(--b)` with no colour of its own: the page's --fg on it.
Each pair must reach 4.5:1 (WCAG 2 contrast ratio) in both schemes. Every font
size is 12px or larger, and an SVG that CSS scales is never drawn below its own
width, so its 12px labels never shrink.

Run: python3 -B -m unittest discover -s tests -p test_stats_contrast.py -v
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import test_stats_redesign as redesign  # noqa: E402

TEMPLATE = ROOT / "site" / "template.html"
MIN_RATIO = 4.5
MIN_PX = 12
DARK = "@media (prefers-color-scheme: dark)"


def css():
    page = TEMPLATE.read_text(encoding="utf-8")
    return page[page.index("<style>") + 7:page.index("</style>")]


def tokens(block):
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", block))


def schemes(text):
    """{"light": tokens, "dark": tokens}."""
    light = text[text.index(":root {"):text.index("}", text.index(":root {"))]
    start = text.index(DARK)
    dark = text[start:text.index("}", start)]
    return {"light": tokens(light), "dark": tokens(dark)}


def rules(text):
    """(selector, {property: value}) for each rule outside :root and the dark-scheme block."""
    start = text.index(DARK)
    end = text.index("}\n}", start) + 3
    text = text[:start] + text[end:]
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"@media[^{]*\{", "", text)
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decls = dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in body.split(";") if ":" in d))
        out.append((" ".join(sel.split()), decls))
    return out


def var(value):
    m = re.fullmatch(r"var\((--[\w-]+)\)", value or "")
    return m.group(1) if m else None


def pairs(text):
    """(selector, text colour token, background token) for every rule that colours text."""
    out = []
    for sel, d in rules(text):
        if sel.startswith(":root"):
            continue
        bg = var(d.get("background")) or "--bg"
        if "color" in d:
            out.append((sel, var(d["color"]), bg))
        elif "fill" in d and re.search(r"\btext\b|\btspan\b", sel):
            out.append((sel, var(d["fill"]), "--bg"))
        elif var(d.get("background")):
            out.append((sel, "--fg", bg))
    return out


def luminance(hex_colour):
    def channel(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(hex_colour[i:i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def px(size):
    m = re.fullmatch(r"([\d.]+)(px|rem|em)", size)
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2)
    return n if u == "px" else n * 16


class ContrastTest(unittest.TestCase):
    def setUp(self):
        self.css = css()
        self.schemes = schemes(self.css)

    def test_one_token_set_redefined_for_dark(self):
        light, dark = self.schemes["light"], self.schemes["dark"]
        self.assertGreater(len(light), 20)
        self.assertEqual(sorted(light), sorted(dark))
        self.assertEqual(len(re.findall(r"#[0-9a-fA-F]{3,6}\b", self.css)), len(light) + len(dark),
                         "a colour outside the token set")

    def test_every_text_colour_reaches_4_5_to_1_in_both_schemes(self):
        found = pairs(self.css)
        self.assertGreater(len(found), 15)
        for scheme, t in self.schemes.items():
            for sel, fg, bg in found:
                self.assertIn(fg, t, sel)
                self.assertIn(bg, t, sel)
                r = ratio(t[fg], t[bg])
                self.assertGreaterEqual(r, MIN_RATIO, f"{scheme}: {sel}: {fg} {t[fg]} on {bg} {t[bg]} is {r:.2f}:1")

    def test_text_inside_a_coloured_cell(self):
        """An answer cell's labels (dt, muted) and the code in it sit on the cell's colour, not on --bg."""
        for scheme, t in self.schemes.items():
            for cell in ("--o-ok", "--o-wait", "--o-no", "--o-none"):
                for fg in ("--muted", "--fg", "--accent"):
                    self.assertGreaterEqual(ratio(t[fg], t[cell]), MIN_RATIO, (scheme, fg, cell))
                self.assertGreaterEqual(ratio(t["--fg"], t["--code"]), MIN_RATIO, scheme)

    def test_the_check_catches_a_low_contrast_label(self):
        bad = self.css.replace("--muted: #5c5c57", "--muted: #b0b0aa", 1)
        t = schemes(bad)["light"]
        self.assertTrue(any(ratio(t[fg], t[bg]) < MIN_RATIO for _, fg, bg in pairs(bad)))
        self.assertAlmostEqual(ratio("#000000", "#ffffff"), 21, places=6)
        self.assertAlmostEqual(ratio("#777777", "#ffffff"), 4.48, places=2)

    def test_the_tiles_text_is_read_on_its_state_colour(self):
        found = {(fg, bg) for _, fg, bg in pairs(self.css)}
        for state in ("--s-green", "--s-red", "--s-not"):
            self.assertIn(("--on-state", state), found)


class LabelSizeTest(unittest.TestCase):
    def test_every_font_size_is_12px_or_larger(self):
        sizes = []
        for sel, d in rules(css()):
            if "font-size" in d:
                v = px(d["font-size"])
                if d["font-size"].endswith("em") and not d["font-size"].endswith("rem"):
                    v = float(d["font-size"][:-2]) * 13.6  # of the smallest text it sits in, .85rem
                self.assertIsNotNone(v, (sel, d["font-size"]))
                self.assertGreaterEqual(v, MIN_PX, (sel, d["font-size"]))
                sizes.append(v)
            if "font" in d:
                m = re.search(r"([\d.]+(?:px|rem))", d["font"])
                self.assertGreaterEqual(px(m.group(1)), MIN_PX, sel)
        self.assertIn(12.0, sizes)  # SVG labels

    def test_svg_labels_are_never_scaled_below_12px(self):
        rule = dict(rules(css()))["svg.multiple"]
        min_w = float(rule["min-width"].removesuffix("px"))
        with tempfile.TemporaryDirectory() as d:
            for page in (redesign.run_build(redesign.DATA, redesign.FINDINGS, Path(d) / "a"),
                         redesign.run_build(redesign.NEGATIVE, redesign.FINDINGS, Path(d) / "b")):
                self.assertNotIn("font-size=", page)  # sizes come from the CSS only
                for tag in re.findall(r"<svg [^>]*>", page):
                    if 'aria-hidden="true"' in tag:
                        continue
                    w = float(re.search(r'viewBox="0 0 ([\d.]+) ', tag).group(1))
                    fixed = re.search(r' width="([\d.]+)"', tag)
                    if fixed:
                        self.assertEqual(float(fixed.group(1)), w, tag)  # drawn 1:1
                    elif 'class="band"' in tag:
                        # Scaled to the content column, never below its min-width: its labels' units shrink by at
                        # most min-width / width, and stay 12px or larger.
                        band = dict(rules(css()))
                        floor = float(band["svg.band"]["min-width"].removesuffix("px")) / w
                        self.assertGreaterEqual(px(band["svg.band text"]["font-size"]) * floor, MIN_PX, tag)
                    else:
                        self.assertIn('class="multiple', tag)
                        self.assertLessEqual(w, min_w, tag)  # scaled up only


if __name__ == "__main__":
    unittest.main()
