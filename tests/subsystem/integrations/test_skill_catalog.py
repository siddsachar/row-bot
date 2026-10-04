"""Skills discovery quality: featured library, fork dedup, plain summaries and publisher signals (fakes only)."""
import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import plans, sources
from row_bot.integrations.safe import TtlCache
from row_bot.skills_hub import source_registry
from row_bot.skills_hub.clawhub_source import parse_clawhub_payload
from row_bot.skills_hub.models import SkillHubEntry

PDF = ("Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting "
       "documents, and handling forms. When Claude needs to fill in a PDF form or process documents at scale.")


def clawhub(owner, *, summary=PDF, downloads=0, stars=0, official=False, repo=None, name="pdf"):
    return {"displayName": name, "slug": "pdf", "ownerHandle": owner, "summary": summary, "downloads": downloads,
            "official": official, "publisher": {"handle": owner, "official": official},
            "native": {"skill": {"stats": {"downloads": downloads, "stars": stars}}},
            "sourceIdentity": {"host": "github" if repo else None, "repo": repo, "owner": owner}}


class Fake:
    """A skill catalog that answers with fixed entries."""
    supports_browse = supports_search = True

    def __init__(self, source_id, entries):
        self.id, self.entries = source_id, entries

    def search(self, query, *, limit):
        return self.entries

    def browse(self, *, limit):
        return self.entries


@pytest.fixture
def skills(tmp_path, monkeypatch, reload_for_data_dir):
    reload_for_data_dir(tmp_path, "row_bot.skills")  # The skill catalog caches live in this test's folder.
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))

    def install(*fakes):
        monkeypatch.setattr(source_registry, "_DEFAULT_REGISTRY", source_registry.SkillSourceRegistry(list(fakes)))
    return install


def search(query, *names):
    return api.search_items(owner_id="owner", query=query, sources=list(names), kind="skill", refresh=True,
                            include_incompatible=False, cursor=None, limit=50)["items"]


@pytest.mark.parametrize(("raw", "clean"), [
    ("Process PDF files �� read, merge, split", "Process PDF files – read, merge, split"),
    ("Itâ€™s a cafÃ© guide", "It’s a café guide"),
    ("Analysis:\n1. **File Q&A**: upload [files](https://x.test) for _quick_ answers\n2. **Dataset Q",
     "Analysis: File Q&A: upload files for quick answers Dataset Q"),
    ("---\nname: x\n---\n# Title\nKeep `snake_case_names` intact <b>here</b>", "Title Keep snake_case_names intact here"),
    ("PDF处理工具", "PDF处理工具"),
])
def test_summaries_are_plain_text_with_the_encoding_glitch_fixed(raw, clean):
    assert sources.plain_text(raw) == clean


def test_clawhub_forks_merge_into_the_official_or_most_used_copy_with_every_attribution(skills):
    entries = parse_clawhub_payload({"results": [
        clawhub("forker", downloads=391), clawhub("popular", downloads=49993, stars=66),
        clawhub("truncated", downloads=5, summary=PDF[:150] + "..."),
        clawhub("different", summary="Turn PDF invoices into spreadsheet rows for accounting teams, one page at a time."),
    ]})
    skills(Fake("clawhub", entries))
    rows = search("pdf", "clawhub")
    assert len(rows) == 2
    merged = next(row for row in rows if len(row["attributions"]) == 3)
    assert merged["publisher"] == "popular" and merged["signals"] == {"downloads": 49993, "stars": 66, "official": False}
    assert {a["publisher"] for a in merged["attributions"]} == {"popular", "forker", "truncated"}


def test_an_official_publisher_copy_leads_its_forks(skills):
    skills(Fake("clawhub", parse_clawhub_payload({"results": [
        clawhub("popular", downloads=49993), clawhub("vendor", official=True, downloads=10)]})))
    (row,) = search("pdf", "clawhub")
    assert row["publisher"] == "vendor" and row["signals"]["official"] and len(row["attributions"]) == 2


def test_github_forks_merge_by_content_hash_and_featured_skills_lead_their_upstream(skills):
    def github(repo, path, sha):
        return SkillHubEntry(f"github:{repo}:{path}:", path.rsplit("/", 1)[-1].title(), "GitHub skill", "github", repo,
                             f"github:{repo}/{path}", author=repo.split("/")[0],
                             metadata={"repository": repo, "path": path, "content_hash": "git:" + sha})
    skills(Fake("github", [github("obra/superpowers", "skills/systematic-debugging", "a" * 40),
                           github("someone/fork", "skills/systematic-debugging", "a" * 40),
                           github("other/repo", "skills/systematic-debugging", "b" * 40)]))
    rows = search("debugging", "featured_skills", "github")
    first = rows[0]
    assert first["source"] == "featured_skills" and first["name"] == "Systematic debugging"
    assert {a["source"] for a in first["attributions"]} == {"featured_skills", "github"}
    assert len(first["attributions"]) == 3  # The featured upstream and its byte-identical fork.
    assert sum(len(row["attributions"]) for row in rows) == 4


def test_featured_skills_open_discover_and_are_added_from_their_pinned_source_after_consent(skills, monkeypatch):
    from row_bot.application import client_skill_hub
    skills(Fake("clawhub", []))
    rows = search("", "featured_skills", "clawhub")
    assert rows and all(row["source"] == "featured_skills" for row in rows[:10])
    assert [row["name"] for row in rows[:3]] == [s["name"] for s in sorted(sources.featured_skills().values(),
                                                                             key=lambda s: s["featured_rank"])[:3]]
    row, reference = sources.catalog_entry("skill:featured:anthropic-skill-creator")
    plan = plans.compute(row, reference)
    assert plan["supported"] and plan["consent"]["downloads"][0].startswith("https://github.com/anthropics/skills/tree/")
    seen = []
    monkeypatch.setattr(client_skill_hub, "preview_skill_reference", lambda **k: seen.append(k) or {
        "scan": {"blocked": False}, "preview_id": "p", "content_hash": "h", "review_files": []})
    record = {"reference": reference}
    assert plans._skill_test(plans.Context("owner", "owner", lambda: None), record, {}) == "done"
    assert seen[0]["install_ref"].startswith("github:anthropics/skills/skills/skill-creator?ref=")
    assert all(len(s["commit"]) == 40 and s["license"] in {"MIT", "Apache-2.0", "MPL-2.0"}
               for s in sources.featured_skills().values())
