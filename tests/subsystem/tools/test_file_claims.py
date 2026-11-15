"""A2 WP4: the files a reply says it saved are checked on disk before the turn ends.

A hit costs the person an extra turn, so only a plain claim of a file that is nowhere under the chat's folders
counts: a name that is there (even in a subfolder), a link, an unclaimed or hedged mention, or a path outside
the folders is no hit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from row_bot.file_claims import claimed_missing, could_claim

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    folder = home / "Documents" / "Row-Bot"
    (folder / "reports").mkdir(parents=True)
    (folder / "reports" / "q3.md").write_text("Q3", encoding="utf-8")
    (folder / "data.csv").write_text("a,b\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return folder


@pytest.mark.parametrize(("reply", "missing"), [
    ("I saved the summary to report.md.", ["report.md"]),
    ("Done! The report is saved as `report.md`.", ["report.md"]),
    ("Saved: `notes/plan.md`", ["notes/plan.md"]),
    ("I've created notes.md with the packing list.", ["notes.md"]),
    ("`summary.md` is ready in your workspace.", ["summary.md"]),
    ("summary.md has been saved.", ["summary.md"]),
    ("**Saved** `final.docx` — ready to send.", ["final.docx"]),
    ("I read data.csv and created summary.md from it.", ["summary.md"]),
    ("I couldn't read input.csv, so I created out.md instead of a table.", ["out.md"]),
    ("Created these files:\n- a.md\n- `b.md`\n2. c.json", ["a.md", "b.md", "c.json"]),
    ("I updated reports/q3.md and saved a copy as reports/q3-copy.md.", ["reports/q3-copy.md"]),
    ("I saved it to ~/Desktop/plan.md.", ["~/Desktop/plan.md"]),
])
def test_a_plain_claim_of_a_file_that_is_not_there_is_a_hit(workspace, reply, missing):
    assert could_claim(reply)
    assert claimed_missing(reply, [workspace]) == missing


@pytest.mark.parametrize("reply", [
    # There: at the path, or under a subfolder when named alone.
    "I wrote the Q3 summary to reports/q3.md.",
    "I wrote the Q3 summary to q3.md.",
    "I saved it to ~/Documents/Row-Bot/data.csv.",
    # Not a claim of something made.
    "I created the chart from input.csv.",
    "Here's the plan for report.md: sections A and B.",
    "You asked about report.md; it isn't there.",
    # Hedged, negated, future or asked.
    "Should I save it as report.md?",
    "I'll save it to report.md next.",
    "I can export it to report.pdf if you want.",
    "I haven't saved report.md yet.",
    "It will be saved to report.md.",
    "I tried to save report.md but the disk was full.",
    "Files I couldn't create:\n- a.md",
    # Links, code and names that aren't files.
    "The full report is saved at https://example.test/reports/report.pdf.",
    "```python\ndf.to_csv('out.csv')\n```\nRun it and you'll have out.csv.",
    "I made it in Node.js.",
    # Outside the folders, or somewhere this can't look.
    "I saved it to ../outside.md.",
    "I saved it to your Desktop as q4.md.",
])
def test_present_files_links_and_unclaimed_or_hedged_mentions_are_no_hit(workspace, reply):
    assert claimed_missing(reply, [workspace]) == []


def test_a_name_in_a_second_folder_counts_as_there(workspace, tmp_path):
    code_folder = tmp_path / "app"
    (code_folder / "src").mkdir(parents=True)
    (code_folder / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")

    reply = "I created src/main.py. I saved main.py with the fix."

    assert claimed_missing(reply, [workspace]) == ["src/main.py", "main.py"]
    assert claimed_missing(reply, [workspace, code_folder]) == []


def test_the_chats_own_files_count_as_there(workspace):
    reply = "I saved the cover as cover.docx and generated sunset.png for it."

    assert claimed_missing(reply, [workspace], known_names={"cover.docx", "generated-image-3.png"}) == []
    assert claimed_missing(reply, [workspace]) == ["cover.docx", "sunset.png"]


def test_an_absolute_path_is_checked_where_it_says(workspace, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "there.md").write_text("x", encoding="utf-8")
    there, gone = elsewhere / "there.md", elsewhere / "gone.md"

    reply = f"I saved `{there}` and also saved `{gone}`."

    assert claimed_missing(reply, [workspace]) == [str(gone)]


def test_a_reply_without_file_names_needs_no_look():
    assert not could_claim("All done: the meeting is moved to Friday at 10.")
