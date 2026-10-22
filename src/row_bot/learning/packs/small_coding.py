"""Pack: small coding tasks (D30), visible split.

Each task asks for one function in ``solution.py``. The verifier runs hidden unittest
tests against it in a sandbox with the network off and a time limit
(``learning.sandbox``); the tests reach the edge cases a careless version misses, and
each task's decoy is such a version.
"""

from __future__ import annotations

from textwrap import dedent

from row_bot.learning.packs import PackTask, Solution, Verifier

_HEADER = "import unittest\nfrom solution import {name}\n\n\nclass Check(unittest.TestCase):\n"


def _tests(name: str, body: str) -> str:
    return _HEADER.format(name=name) + dedent(body).strip("\n").replace("\n", "\n    ").join(("    ", "\n"))


def _task(number: int, name: str, prompt: str, tests: str, reference: str, decoy: str,
          files: dict[str, str] | None = None) -> PackTask:
    return PackTask(
        id=f"code_{number:02d}", pack="small_coding", split="visible",
        prompt=f"In the workspace, write solution.py with {prompt} Use only the Python standard library. "
               "When the file is written, reply with: done",
        verifier=Verifier("code_tests", _tests(name, tests), path="solution.py"),
        reference=Solution(answer="done", files={"solution.py": dedent(reference).lstrip()}),
        decoy=Solution(answer="done", files={"solution.py": dedent(decoy).lstrip()}),
        files=files or {}, tools=("files",), max_steps=8,
    )


TASKS: tuple[PackTask, ...] = (
    _task(1, "slugify", "a function slugify(text) that lower-cases text, turns every run of characters that are "
             "not letters or digits into a single hyphen, and strips hyphens from both ends.", """
        def test_basic(self):
            self.assertEqual(slugify("Hello, World!"), "hello-world")
        def test_runs(self):
            self.assertEqual(slugify("  A -- B__C  "), "a-b-c")
        def test_digits(self):
            self.assertEqual(slugify("Q3 2026 Report"), "q3-2026-report")
        def test_empty(self):
            self.assertEqual(slugify("!!!"), "")
        """, """
        import re

        def slugify(text):
            return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        """, """
        def slugify(text):
            return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
        """),
    _task(2, "to_roman", "a function to_roman(number) that returns the Roman numeral for an integer from 1 to 3999 "
             "and raises ValueError outside that range.", """
        def test_small(self):
            self.assertEqual(to_roman(4), "IV")
            self.assertEqual(to_roman(9), "IX")
        def test_large(self):
            self.assertEqual(to_roman(1994), "MCMXCIV")
            self.assertEqual(to_roman(3999), "MMMCMXCIX")
        def test_range(self):
            with self.assertRaises(ValueError):
                to_roman(0)
            with self.assertRaises(ValueError):
                to_roman(4000)
        """, """
        def to_roman(number):
            if not 1 <= number <= 3999:
                raise ValueError(number)
            parts = []
            for value, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                                   (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
                while number >= value:
                    parts.append(letters)
                    number -= value
            return "".join(parts)
        """, """
        def to_roman(number):
            parts = []
            for value, letters in ((1000, "M"), (500, "D"), (100, "C"), (50, "L"), (10, "X"), (5, "V"), (1, "I")):
                while number >= value:
                    parts.append(letters)
                    number -= value
            return "".join(parts)
        """),
    _task(3, "rle", "a function rle(text) that run-length encodes a string as each character followed by its "
             "count, for example 'aaabcc' becomes 'a3b1c2'.", """
        def test_example(self):
            self.assertEqual(rle("aaabcc"), "a3b1c2")
        def test_single(self):
            self.assertEqual(rle("z"), "z1")
        def test_empty(self):
            self.assertEqual(rle(""), "")
        def test_long_run(self):
            self.assertEqual(rle("x" * 12 + "y"), "x12y1")
        """, """
        from itertools import groupby

        def rle(text):
            return "".join(f"{char}{len(list(run))}" for char, run in groupby(text))
        """, """
        def rle(text):
            out, count = [], 1
            for index in range(1, len(text)):
                if text[index] == text[index - 1]:
                    count += 1
                else:
                    out.append(f"{text[index - 1]}{count}")
                    count = 1
            return "".join(out)
        """),
    _task(4, "top_words", "a function top_words(text, k) that returns the k most common words (lower-cased, "
             "words are runs of letters) as a list, most common first and ties in alphabetical order.", """
        def test_order(self):
            self.assertEqual(top_words("b a b c a b", 2), ["b", "a"])
        def test_ties(self):
            self.assertEqual(top_words("pear apple fig", 2), ["apple", "fig"])
        def test_case_and_punctuation(self):
            self.assertEqual(top_words("Dog, dog! CAT? cat; dog.", 1), ["dog"])
        def test_more_than_words(self):
            self.assertEqual(top_words("one", 3), ["one"])
        """, """
        import re
        from collections import Counter

        def top_words(text, k):
            counts = Counter(re.findall(r"[a-z]+", text.lower()))
            return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:k]]
        """, """
        import re
        from collections import Counter

        def top_words(text, k):
            return [word for word, _ in Counter(re.findall(r"[a-z]+", text.lower())).most_common(k)]
        """),
    _task(5, "parse_duration", "a function parse_duration(text) that turns a duration such as '1h30m15s', '45m' "
             "or '2h' into a number of seconds, and raises ValueError for anything else (including '').", """
        def test_full(self):
            self.assertEqual(parse_duration("1h30m15s"), 5415)
        def test_parts(self):
            self.assertEqual(parse_duration("45m"), 2700)
            self.assertEqual(parse_duration("2h"), 7200)
            self.assertEqual(parse_duration("90s"), 90)
        def test_bad(self):
            for bad in ("", "1x", "h", "1h 30m", "30m1h"):
                with self.assertRaises(ValueError):
                    parse_duration(bad)
        """, """
        import re

        def parse_duration(text):
            match = re.fullmatch(r"(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?", text)
            if not text or match is None:
                raise ValueError(text)
            hours, minutes, seconds = (int(part or 0) for part in match.groups())
            return hours * 3600 + minutes * 60 + seconds
        """, """
        import re

        def parse_duration(text):
            total = 0
            for amount, unit in re.findall(r"(\\d+)([hms])", text):
                total += int(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
            return total
        """),
    _task(6, "balanced", "a function balanced(text) that returns True when the brackets (), [] and {} in text are "
             "balanced and properly nested (other characters are ignored), else False.", """
        def test_good(self):
            self.assertTrue(balanced("a(b[c]{d})"))
            self.assertTrue(balanced(""))
        def test_bad(self):
            self.assertFalse(balanced("(]"))
            self.assertFalse(balanced("([)]"))
            self.assertFalse(balanced("(("))
            self.assertFalse(balanced("}"))
        """, """
        def balanced(text):
            pairs, stack = {")": "(", "]": "[", "}": "{"}, []
            for char in text:
                if char in "([{":
                    stack.append(char)
                elif char in pairs:
                    if not stack or stack.pop() != pairs[char]:
                        return False
            return not stack
        """, """
        def balanced(text):
            return all(text.count(a) == text.count(b) for a, b in ("()", "[]", "{}"))
        """),
    _task(7, "merge_intervals", "a function merge_intervals(intervals) that takes a list of [start, end] pairs and "
             "returns the merged, sorted list of pairs; intervals that touch (end equals the next start) merge.",
          """
        def test_overlap(self):
            self.assertEqual(merge_intervals([[1, 3], [2, 6], [8, 10]]), [[1, 6], [8, 10]])
        def test_unsorted_and_touching(self):
            self.assertEqual(merge_intervals([[5, 7], [1, 2], [2, 4]]), [[1, 4], [5, 7]])
        def test_contained(self):
            self.assertEqual(merge_intervals([[1, 10], [2, 3]]), [[1, 10]])
        def test_empty(self):
            self.assertEqual(merge_intervals([]), [])
        """, """
        def merge_intervals(intervals):
            merged = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            return merged
        """, """
        def merge_intervals(intervals):
            merged = []
            for start, end in sorted(intervals):
                if merged and start < merged[-1][1]:
                    merged[-1][1] = end
                else:
                    merged.append([start, end])
            return merged
        """),
    _task(8, "flatten", "a function flatten(items) that flattens arbitrarily nested lists into one list, keeping "
             "order; strings are not split.", """
        def test_nested(self):
            self.assertEqual(flatten([1, [2, [3, [4]]], 5]), [1, 2, 3, 4, 5])
        def test_strings(self):
            self.assertEqual(flatten(["ab", ["cd"]]), ["ab", "cd"])
        def test_empty(self):
            self.assertEqual(flatten([[], [[]]]), [])
        """, """
        def flatten(items):
            out = []
            for item in items:
                if isinstance(item, list):
                    out.extend(flatten(item))
                else:
                    out.append(item)
            return out
        """, """
        def flatten(items):
            out = []
            for item in items:
                if isinstance(item, list):
                    out.extend(item)
                else:
                    out.append(item)
            return out
        """),
    _task(9, "camel_to_snake", "a function camel_to_snake(name) that turns camelCase or PascalCase names into "
             "snake_case, keeping acronyms together: 'parseHTTPResponse' becomes 'parse_http_response'.", """
        def test_camel(self):
            self.assertEqual(camel_to_snake("userId"), "user_id")
        def test_pascal(self):
            self.assertEqual(camel_to_snake("OrderLineItem"), "order_line_item")
        def test_acronym(self):
            self.assertEqual(camel_to_snake("parseHTTPResponse"), "parse_http_response")
            self.assertEqual(camel_to_snake("getURL"), "get_url")
        def test_digits(self):
            self.assertEqual(camel_to_snake("version2Name"), "version2_name")
        """, """
        import re

        def camel_to_snake(name):
            name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", name)
            name = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name)
            return name.lower()
        """, """
        def camel_to_snake(name):
            return "".join("_" + c.lower() if c.isupper() else c for c in name).lstrip("_")
        """),
    _task(10, "chunk", "a function chunk(items, size) that splits a list into consecutive lists of length size "
              "(the last may be shorter), and raises ValueError when size is below 1.", """
        def test_even(self):
            self.assertEqual(chunk([1, 2, 3, 4], 2), [[1, 2], [3, 4]])
        def test_rest(self):
            self.assertEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        def test_empty(self):
            self.assertEqual(chunk([], 3), [])
        def test_bad_size(self):
            with self.assertRaises(ValueError):
                chunk([1], 0)
        """, """
        def chunk(items, size):
            if size < 1:
                raise ValueError(size)
            return [items[index:index + size] for index in range(0, len(items), size)]
        """, """
        def chunk(items, size):
            return [items[index:index + size] for index in range(0, len(items) - size + 1, size)]
        """),
    _task(11, "caesar", "a function caesar(text, shift) that shifts every letter by shift places in the alphabet "
              "(wrapping around, keeping upper and lower case) and leaves other characters as they are.", """
        def test_shift(self):
            self.assertEqual(caesar("abc", 1), "bcd")
        def test_wrap_and_case(self):
            self.assertEqual(caesar("Xyz", 3), "Abc")
        def test_other_characters(self):
            self.assertEqual(caesar("Hi, Bo!", 2), "Jk, Dq!")
        def test_negative(self):
            self.assertEqual(caesar("bcd", -1), "abc")
        """, """
        def caesar(text, shift):
            out = []
            for char in text:
                if char.isascii() and char.isalpha():
                    base = ord("A") if char.isupper() else ord("a")
                    out.append(chr(base + (ord(char) - base + shift) % 26))
                else:
                    out.append(char)
            return "".join(out)
        """, """
        def caesar(text, shift):
            return "".join(chr(ord(c) + shift) if c.isalpha() else c for c in text)
        """),
    _task(12, "median", "a function median(numbers) that returns the median of a list of numbers as a float, and "
              "raises ValueError for an empty list.", """
        def test_odd(self):
            self.assertEqual(median([3, 1, 2]), 2.0)
        def test_even(self):
            self.assertEqual(median([4, 1, 3, 2]), 2.5)
        def test_one(self):
            self.assertEqual(median([7]), 7.0)
        def test_empty(self):
            with self.assertRaises(ValueError):
                median([])
        """, """
        def median(numbers):
            values = sorted(numbers)
            if not values:
                raise ValueError("no numbers")
            middle = len(values) // 2
            if len(values) % 2:
                return float(values[middle])
            return (values[middle - 1] + values[middle]) / 2
        """, """
        def median(numbers):
            return float(sorted(numbers)[len(numbers) // 2])
        """),
    _task(13, "dedupe", "a function dedupe(items) that removes repeated items from a list, keeping the first "
              "occurrence of each and the original order.", """
        def test_order(self):
            self.assertEqual(dedupe([3, 1, 3, 2, 1]), [3, 1, 2])
        def test_strings(self):
            self.assertEqual(dedupe(["b", "a", "b"]), ["b", "a"])
        def test_empty(self):
            self.assertEqual(dedupe([]), [])
        """, """
        def dedupe(items):
            seen, out = set(), []
            for item in items:
                if item not in seen:
                    seen.add(item)
                    out.append(item)
            return out
        """, """
        def dedupe(items):
            return sorted(set(items))
        """),
    _task(14, "is_valid_isbn10", "a function is_valid_isbn10(text) that checks an ISBN-10: hyphens are ignored, "
              "there must be 10 characters, the first nine digits, the last a digit or X (worth 10), and the sum "
              "of each value times its position (1 to 10) must be divisible by 11.", """
        def test_valid(self):
            self.assertTrue(is_valid_isbn10("0-306-40615-2"))
            self.assertTrue(is_valid_isbn10("0-8044-2957-X"))
        def test_invalid(self):
            self.assertFalse(is_valid_isbn10("0-306-40615-3"))
            self.assertFalse(is_valid_isbn10("X-306-40615-1"))
            self.assertFalse(is_valid_isbn10("12345"))
        """, """
        def is_valid_isbn10(text):
            chars = text.replace("-", "")
            if len(chars) != 10 or not chars[:9].isdigit() or not (chars[9].isdigit() or chars[9] == "X"):
                return False
            values = [int(c) for c in chars[:9]] + [10 if chars[9] == "X" else int(chars[9])]
            return sum(value * position for position, value in enumerate(values, start=1)) % 11 == 0
        """, """
        def is_valid_isbn10(text):
            chars = text.replace("-", "")
            if len(chars) != 10:
                return False
            values = [10 if c == "X" else int(c) for c in chars]
            return sum(value * position for position, value in enumerate(values, start=1)) % 11 == 0
        """),
    _task(15, "second_largest", "a function second_largest(numbers) that returns the second largest distinct value "
              "in a list, or None when there is none.", """
        def test_basic(self):
            self.assertEqual(second_largest([5, 1, 4]), 4)
        def test_duplicates(self):
            self.assertEqual(second_largest([7, 7, 3]), 3)
        def test_none(self):
            self.assertIsNone(second_largest([2, 2]))
            self.assertIsNone(second_largest([]))
        """, """
        def second_largest(numbers):
            values = sorted(set(numbers), reverse=True)
            return values[1] if len(values) > 1 else None
        """, """
        def second_largest(numbers):
            values = sorted(numbers, reverse=True)
            return values[1] if len(values) > 1 else None
        """),
    _task(16, "word_wrap", "a function word_wrap(text, width) that splits text into lines of at most width "
              "characters, breaking only between words (single spaces join words on a line); a word longer than "
              "width goes on its own line.", """
        def test_wrap(self):
            self.assertEqual(word_wrap("the quick brown fox", 10), ["the quick", "brown fox"])
        def test_long_word(self):
            self.assertEqual(word_wrap("a extraordinary b", 5), ["a", "extraordinary", "b"])
        def test_spaces(self):
            self.assertEqual(word_wrap("  one   two  ", 7), ["one two"])
        def test_empty(self):
            self.assertEqual(word_wrap("", 5), [])
        """, """
        def word_wrap(text, width):
            lines, line = [], ""
            for word in text.split():
                if line and len(line) + 1 + len(word) <= width:
                    line += " " + word
                else:
                    if line:
                        lines.append(line)
                    line = word
            if line:
                lines.append(line)
            return lines
        """, """
        def word_wrap(text, width):
            lines, line = [], ""
            for word in text.split(" "):
                if len(line) + len(word) < width:
                    line = (line + " " + word).strip()
                else:
                    lines.append(line)
                    line = word
            return lines + [line]
        """),
)
