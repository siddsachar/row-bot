import json


def test_wikipedia_tool_returns_recoverable_message_on_json_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / ".row-bot"))
    from row_bot.tools.wikipedia_tool import WikipediaTool

    class BrokenRetriever:
        def invoke(self, query):
            raise json.JSONDecodeError("Expecting value", "", 0)

    tool = WikipediaTool()
    monkeypatch.setattr(tool, "get_retriever", lambda **kwargs: BrokenRetriever())

    result = tool.execute("archives")

    assert "temporarily unavailable" in result
    assert "Do not retry the Wikipedia tool" in result
    assert "answer from general knowledge" in result


def test_wikipedia_tool_description_discourages_broad_general_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / ".row-bot"))
    from row_bot.tools.wikipedia_tool import WikipediaTool

    description = WikipediaTool().description.lower()

    assert "specific encyclopedia lookups" in description
    assert "broad conceptual questions directly" in description


def test_wikipedia_tool_forces_https_api_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / ".row-bot"))
    import wikipedia.wikipedia as wiki_impl
    from row_bot.tools.wikipedia_tool import WikipediaTool

    original_url = wiki_impl.API_URL
    wiki_impl.API_URL = "http://en.wikipedia.org/w/api.php"
    try:
        WikipediaTool().get_retriever()
        assert wiki_impl.API_URL == "https://en.wikipedia.org/w/api.php"
    finally:
        wiki_impl.API_URL = original_url
