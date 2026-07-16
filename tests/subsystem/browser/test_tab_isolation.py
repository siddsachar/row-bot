from __future__ import annotations

from row_bot.tools.browser_tool import BrowserSession


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def is_closed(self):
        return self.closed

    def title(self):
        return self.url

    def bring_to_front(self):
        return None

    def close(self):
        self.closed = True

    def evaluate(self, _script):
        return {"url": self.url, "title": self.url, "refs": [], "refCount": 0, "skipped": 0}


class _Context:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        page = _Page("about:blank")
        self.pages.append(page)
        return page


def test_two_threads_cannot_list_switch_or_close_each_others_tabs() -> None:
    session = BrowserSession()
    a1, a2, b1 = _Page("https://a/one"), _Page("https://a/two"), _Page("https://b/one")
    session._context = _Context([a1, a2, b1])
    session._launched = True
    session._thread_pages = {"a": a1, "b": b1}
    session._page_owners = {a1: "a", a2: "a", b1: "b"}
    session._run_on_pw_thread = lambda fn: fn()

    listed = session.tab_action("list", thread_id="a")
    assert "https://a/one" in listed and "https://a/two" in listed
    assert "https://b/one" not in listed
    assert "Invalid tab_id" in session.tab_action("switch", tab_id=2, thread_id="a")
    assert "Invalid tab_id" in session.tab_action("close", tab_id=2, thread_id="a")


def test_new_tab_keeps_prior_tab_owned_by_same_thread() -> None:
    session = BrowserSession()
    first = _Page("https://a/one")
    session._context = _Context([first])
    session._launched = True
    session._thread_pages = {"a": first}
    session._page_owners = {first: "a"}
    session._run_on_pw_thread = lambda fn: fn()
    session.tab_action("new", thread_id="a")
    assert len([page for page, owner in session._page_owners.items() if owner == "a"]) == 2
