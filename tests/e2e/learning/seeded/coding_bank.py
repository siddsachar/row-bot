"""The seeded people's small coding requests: one function each, with tests, a reference and a decoy.

Plain data (no Row-Bot import). Each problem names the function, says what it does (the request's
words), holds unittest tests the request is checked with, a reference that passes them and a decoy (a
plausible mistake) that fails them. The generator puts the function in the person's own module path
(``tools/clamp.py``, ``scripts/vat.py``), so the tests load the module from its file, wherever the
sandbox puts the workspace. None of these is one of the task packs' functions.
"""

from __future__ import annotations

from textwrap import dedent

#: Loads the requested module from its file next to the test (works with ``python -I``).
TEST_HEADER = """import importlib.util
import pathlib
import unittest

_SPEC = importlib.util.spec_from_file_location("seeded_module", pathlib.Path(__file__).resolve().parent / {path!r})
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
{name} = getattr(_MODULE, {name!r})


class Check(unittest.TestCase):
"""


def tests_for(name: str, path: str, body: str) -> str:
    """The unittest file for one problem, loading ``name`` from ``path``."""

    lines = dedent(body).strip("\n").splitlines()
    return TEST_HEADER.format(name=name, path=path) + "\n".join("    " + line if line else "" for line in lines) + "\n"


PROBLEMS: tuple[dict[str, str], ...] = (
    # ── Bookkeeping helpers ─────────────────────────────────────────────
    {
        "id": "vat_breakdown", "domain": "finance", "module": "vat", "name": "vat_breakdown",
        "description": "vat_breakdown(gross, rate) that splits a VAT-inclusive amount into (net, vat), each rounded "
                       "to 2 decimal places, where rate is a fraction like 0.2; with rate 0 the VAT is 0.0.",
        "tests": """
            def test_standard(self):
                self.assertEqual(vat_breakdown(120, 0.2), (100.0, 20.0))
            def test_rounding(self):
                self.assertEqual(vat_breakdown(10, 0.2), (8.33, 1.67))
            def test_zero_rate(self):
                self.assertEqual(vat_breakdown(55.5, 0), (55.5, 0.0))
        """,
        "reference": """
            def vat_breakdown(gross, rate):
                net = round(gross / (1 + rate), 2)
                return net, round(gross - net, 2)
        """,
        "decoy": """
            def vat_breakdown(gross, rate):
                vat = round(gross * rate, 2)
                return round(gross - vat, 2), vat
        """,
    },
    {
        "id": "days_late", "domain": "finance", "module": "dates", "name": "days_late",
        "description": "days_late(due, paid) that takes two ISO dates (YYYY-MM-DD) and returns how many days after "
                       "the due date the payment came, or 0 when it was paid on time or early.",
        "tests": """
            def test_late(self):
                self.assertEqual(days_late("2026-03-01", "2026-03-15"), 14)
            def test_across_months(self):
                self.assertEqual(days_late("2026-01-30", "2026-02-02"), 3)
            def test_early(self):
                self.assertEqual(days_late("2026-05-10", "2026-05-01"), 0)
        """,
        "reference": """
            from datetime import date

            def days_late(due, paid):
                return max(0, (date.fromisoformat(paid) - date.fromisoformat(due)).days)
        """,
        "decoy": """
            from datetime import date

            def days_late(due, paid):
                return abs((date.fromisoformat(paid) - date.fromisoformat(due)).days)
        """,
    },
    {
        "id": "split_bill", "domain": "finance", "module": "split", "name": "split_bill",
        "description": "split_bill(total, parts) that splits an amount into `parts` amounts of whole pence (2 "
                       "decimal places) that add up exactly to the total, differ by at most 0.01, with any extra "
                       "pence going to the first parts.",
        "tests": """
            def test_even(self):
                self.assertEqual(split_bill(30, 3), [10.0, 10.0, 10.0])
            def test_remainder(self):
                self.assertEqual(split_bill(10, 3), [3.34, 3.33, 3.33])
            def test_sum(self):
                self.assertAlmostEqual(sum(split_bill(100.01, 7)), 100.01, places=6)
        """,
        "reference": """
            def split_bill(total, parts):
                pence = round(total * 100)
                base, extra = divmod(pence, parts)
                return [(base + (1 if index < extra else 0)) / 100 for index in range(parts)]
        """,
        "decoy": """
            def split_bill(total, parts):
                return [round(total / parts, 2)] * parts
        """,
    },
    {
        "id": "invoice_total", "domain": "finance", "module": "invoices", "name": "invoice_total",
        "description": "invoice_total(lines) where each line is a dict with qty, unit_price and an optional "
                       "discount_pct; it returns the total after each line's discount, rounded to 2 decimal places.",
        "tests": """
            def test_plain(self):
                self.assertEqual(invoice_total([{"qty": 2, "unit_price": 3.5}, {"qty": 1, "unit_price": 10}]), 17.0)
            def test_discount(self):
                self.assertEqual(invoice_total([{"qty": 4, "unit_price": 25, "discount_pct": 10}]), 90.0)
            def test_empty(self):
                self.assertEqual(invoice_total([]), 0.0)
        """,
        "reference": """
            def invoice_total(lines):
                total = 0.0
                for line in lines:
                    amount = line["qty"] * line["unit_price"]
                    total += amount * (1 - line.get("discount_pct", 0) / 100)
                return round(total, 2)
        """,
        "decoy": """
            def invoice_total(lines):
                return round(sum(line["qty"] * line["unit_price"] for line in lines), 2)
        """,
    },
    {
        "id": "late_fee", "domain": "finance", "module": "fees", "name": "late_fee",
        "description": "late_fee(amount, months_late, rate_pct) that returns amount x months_late x rate_pct / 100 "
                       "rounded to 2 decimal places, and 0.0 when the payment is not late (months_late of 0 or less).",
        "tests": """
            def test_fee(self):
                self.assertEqual(late_fee(1000, 2, 3), 60.0)
            def test_not_late(self):
                self.assertEqual(late_fee(500, 0, 3), 0.0)
                self.assertEqual(late_fee(500, -1, 3), 0.0)
            def test_rounding(self):
                self.assertEqual(late_fee(333.33, 1, 2), 6.67)
        """,
        "reference": """
            def late_fee(amount, months_late, rate_pct):
                if months_late <= 0:
                    return 0.0
                return round(amount * months_late * rate_pct / 100, 2)
        """,
        "decoy": """
            def late_fee(amount, months_late, rate_pct):
                return round(amount * months_late * rate_pct / 100, 2)
        """,
    },
    # ── Game tooling helpers ────────────────────────────────────────────
    {
        "id": "clamp", "domain": "games", "module": "mathutil", "name": "clamp",
        "description": "clamp(value, low, high) that returns value limited to the range low..high and raises "
                       "ValueError when low is greater than high.",
        "tests": """
            def test_inside(self):
                self.assertEqual(clamp(5, 0, 10), 5)
            def test_edges(self):
                self.assertEqual(clamp(-3, 0, 10), 0)
                self.assertEqual(clamp(12.5, 0, 10), 10)
            def test_bad_range(self):
                with self.assertRaises(ValueError):
                    clamp(1, 5, 2)
        """,
        "reference": """
            def clamp(value, low, high):
                if low > high:
                    raise ValueError("low is greater than high")
                return max(low, min(high, value))
        """,
        "decoy": """
            def clamp(value, low, high):
                return max(low, min(high, value))
        """,
    },
    {
        "id": "parse_version", "domain": "games", "module": "versions", "name": "parse_version",
        "description": "parse_version(text) that reads a version like '1.2.3', 'v2.0' or '3' into a tuple of three "
                       "ints (missing parts are 0, a leading 'v' is allowed) and raises ValueError for anything else.",
        "tests": """
            def test_full(self):
                self.assertEqual(parse_version("1.2.3"), (1, 2, 3))
            def test_short(self):
                self.assertEqual(parse_version("v2.0"), (2, 0, 0))
                self.assertEqual(parse_version("3"), (3, 0, 0))
            def test_bad(self):
                with self.assertRaises(ValueError):
                    parse_version("1.x")
                with self.assertRaises(ValueError):
                    parse_version("1.2.3.4")
        """,
        "reference": """
            def parse_version(text):
                parts = text.strip().removeprefix("v").split(".")
                if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
                    raise ValueError(text)
                numbers = [int(part) for part in parts] + [0] * (3 - len(parts))
                return tuple(numbers)
        """,
        "decoy": """
            def parse_version(text):
                parts = [int(part) for part in text.strip().lstrip("v").split(".")]
                return tuple((parts + [0, 0, 0])[:3])
        """,
    },
    {
        "id": "frames_to_timecode", "domain": "games", "module": "timecode", "name": "frames_to_timecode",
        "description": "frames_to_timecode(frames, fps) that turns a frame count into 'MM:SS:FF' (minutes, seconds "
                       "and the frame within the second, each two digits).",
        "tests": """
            def test_zero(self):
                self.assertEqual(frames_to_timecode(0, 30), "00:00:00")
            def test_seconds(self):
                self.assertEqual(frames_to_timecode(95, 30), "00:03:05")
            def test_minutes(self):
                self.assertEqual(frames_to_timecode(60 * 24 * 2 + 25, 24), "02:01:01")
        """,
        "reference": """
            def frames_to_timecode(frames, fps):
                seconds, frame = divmod(frames, fps)
                minutes, seconds = divmod(seconds, 60)
                return f"{minutes:02d}:{seconds:02d}:{frame:02d}"
        """,
        "decoy": """
            def frames_to_timecode(frames, fps):
                seconds = frames // fps
                return f"{seconds // 60:02d}:{seconds:02d}:{frames % fps:02d}"
        """,
    },
    {
        "id": "tile_counts", "domain": "games", "module": "tiles", "name": "tile_counts",
        "description": "tile_counts(tiles) that takes a list of tile dicts with a 'type' key and returns a dict of "
                       "how many tiles there are of each type, with the type names lower-cased.",
        "tests": """
            def test_counts(self):
                tiles = [{"type": "grass"}, {"type": "Water"}, {"type": "grass"}]
                self.assertEqual(tile_counts(tiles), {"grass": 2, "water": 1})
            def test_empty(self):
                self.assertEqual(tile_counts([]), {})
        """,
        "reference": """
            def tile_counts(tiles):
                counts = {}
                for tile in tiles:
                    kind = tile["type"].lower()
                    counts[kind] = counts.get(kind, 0) + 1
                return counts
        """,
        "decoy": """
            def tile_counts(tiles):
                counts = {}
                for tile in tiles:
                    counts[tile["type"]] = counts.get(tile["type"], 0) + 1
                return counts
        """,
    },
    {
        "id": "bounding_box", "domain": "games", "module": "geometry", "name": "bounding_box",
        "description": "bounding_box(points) that takes (x, y) pairs and returns (min_x, min_y, max_x, max_y), or "
                       "None for an empty list.",
        "tests": """
            def test_box(self):
                self.assertEqual(bounding_box([(3, 4), (-1, 9), (5, 2)]), (-1, 2, 5, 9))
            def test_single(self):
                self.assertEqual(bounding_box([(7, 7)]), (7, 7, 7, 7))
            def test_empty(self):
                self.assertIsNone(bounding_box([]))
        """,
        "reference": """
            def bounding_box(points):
                if not points:
                    return None
                xs = [x for x, _ in points]
                ys = [y for _, y in points]
                return min(xs), min(ys), max(xs), max(ys)
        """,
        "decoy": """
            def bounding_box(points):
                xs = [x for x, _ in points]
                ys = [y for _, y in points]
                return min(xs), min(ys), max(xs), max(ys)
        """,
    },
    {
        "id": "neighbours", "domain": "games", "module": "grid", "name": "neighbours",
        "description": "neighbours(x, y, width, height) that returns the up, down, left and right neighbours of a "
                       "cell that lie inside a width x height grid, as a sorted list of (x, y) tuples.",
        "tests": """
            def test_middle(self):
                self.assertEqual(neighbours(1, 1, 3, 3), [(0, 1), (1, 0), (1, 2), (2, 1)])
            def test_corner(self):
                self.assertEqual(neighbours(0, 0, 3, 3), [(0, 1), (1, 0)])
            def test_far_edge(self):
                self.assertEqual(neighbours(2, 1, 3, 2), [(1, 1), (2, 0)])
        """,
        "reference": """
            def neighbours(x, y, width, height):
                found = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                return sorted((a, b) for a, b in found if 0 <= a < width and 0 <= b < height)
        """,
        "decoy": """
            def neighbours(x, y, width, height):
                found = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                return sorted((a, b) for a, b in found if 0 <= a <= width and 0 <= b <= height)
        """,
    },
    {
        "id": "lerp", "domain": "games", "module": "easing", "name": "lerp",
        "description": "lerp(a, b, t) that interpolates linearly from a to b, with t limited to 0..1 first.",
        "tests": """
            def test_middle(self):
                self.assertEqual(lerp(0, 10, 0.25), 2.5)
            def test_limits(self):
                self.assertEqual(lerp(2, 4, -1), 2)
                self.assertEqual(lerp(2, 4, 3), 4)
        """,
        "reference": """
            def lerp(a, b, t):
                t = max(0.0, min(1.0, t))
                return a + (b - a) * t
        """,
        "decoy": """
            def lerp(a, b, t):
                return a + (b - a) * t
        """,
    },
    {
        "id": "grid_rows", "domain": "games", "module": "preview", "name": "grid_rows",
        "description": "grid_rows(tiles, width, height) that draws a level as a list of `height` strings of "
                       "`width` characters: each tile (a dict with x, y and type) shows the first letter of its type, "
                       "and empty cells are '.'.",
        "tests": """
            def test_draw(self):
                tiles = [{"x": 0, "y": 0, "type": "grass"}, {"x": 2, "y": 1, "type": "water"}]
                self.assertEqual(grid_rows(tiles, 3, 2), ["g..", "..w"])
            def test_empty(self):
                self.assertEqual(grid_rows([], 2, 2), ["..", ".."])
        """,
        "reference": """
            def grid_rows(tiles, width, height):
                rows = [["."] * width for _ in range(height)]
                for tile in tiles:
                    rows[tile["y"]][tile["x"]] = tile["type"][0]
                return ["".join(row) for row in rows]
        """,
        "decoy": """
            def grid_rows(tiles, width, height):
                rows = [["."] * width for _ in range(height)]
                for tile in tiles:
                    rows[tile["x"]][tile["y"]] = tile["type"][0]
                return ["".join(row) for row in rows]
        """,
    },
    {
        "id": "version_bump", "domain": "games", "module": "release", "name": "version_bump",
        "description": "version_bump(version, part) that bumps 'major', 'minor' or 'patch' of a version string like "
                       "'1.4.2' and resets the parts after it to 0, raising ValueError for any other part.",
        "tests": """
            def test_minor(self):
                self.assertEqual(version_bump("1.4.2", "minor"), "1.5.0")
            def test_major(self):
                self.assertEqual(version_bump("1.4.2", "major"), "2.0.0")
            def test_patch(self):
                self.assertEqual(version_bump("1.4.2", "patch"), "1.4.3")
            def test_bad(self):
                with self.assertRaises(ValueError):
                    version_bump("1.4.2", "build")
        """,
        "reference": """
            def version_bump(version, part):
                major, minor, patch = (int(item) for item in version.split("."))
                if part == "major":
                    return f"{major + 1}.0.0"
                if part == "minor":
                    return f"{major}.{minor + 1}.0"
                if part == "patch":
                    return f"{major}.{minor}.{patch + 1}"
                raise ValueError(part)
        """,
        "decoy": """
            def version_bump(version, part):
                major, minor, patch = (int(item) for item in version.split("."))
                if part == "major":
                    return f"{major + 1}.{minor}.{patch}"
                if part == "minor":
                    return f"{major}.{minor + 1}.{patch}"
                return f"{major}.{minor}.{patch + 1}"
        """,
    },
)


def problems(domain: str) -> list[dict[str, str]]:
    """The problems of one domain (``finance``, ``games``), in a fixed order."""

    return [problem for problem in PROBLEMS if problem["domain"] == domain]


def code(text: str) -> str:
    """A reference or decoy as the file it becomes."""

    return dedent(text).lstrip("\n")
