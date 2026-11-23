"""The outputs list (W3): one list behind a chat's Outputs pane, Library › Media and the phone's Files.

Each thing made is one output with its versions inside; working files and the person's own files are
listed only when asked for. Files kept before roles are told by their names. Real attachment storage in
an isolated data folder; nothing leaves the machine.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff" + b"0" * 64
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 64


def _mp4(n: int) -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + bytes([n]) * 64


@pytest.fixture
def chats(service):
    """A chat that made a film (three versions, the second presented), an AI picture and working files on the
    way, with a file the person added and files kept before roles; and a second chat with one picture."""
    from row_bot import threads
    from row_bot.application.attachments import register_attachment, set_role

    film = threads.create_thread("Sam's birthday", seed_default_skills=False)
    other = threads.create_thread("Logo ideas", seed_default_skills=False)
    # Kept before roles: an AI clip, a recording and an edit's frames sheet with one of its frames.
    register_attachment(film, "generated-video.mp4", _mp4(9))
    register_attachment(film, "generated-recording.mp4", _mp4(8))
    register_attachment(film, "generated-signup-sheet.jpg", JPG)
    register_attachment(film, "signup-01.jpg", JPG + b"1")
    register_attachment(film, "Party guests £480 café.pdf", b"%PDF-1.4 guests")
    register_attachment(film, "voice-1.wav", WAV, role="working")
    register_attachment(film, "cake-on-a-table.png", PNG, role="final")
    versions = [register_attachment(film, "Birthday montage.mp4", _mp4(n), role="draft", output_id="vid-0001",
                                    version=n) for n in (1, 2)]
    register_attachment(film, "Birthday montage review.jpg", JPG + b"r", role="working", output_id="vid-0001")
    set_role(versions[1]["attachment_ref"], "final")  # Presented.
    newest = register_attachment(film, "Birthday montage.mp4", _mp4(3), role="draft", output_id="vid-0001",
                                 version=3)
    register_attachment(other, "logo-on-white.png", PNG + b"2", role="final")
    return {"film": film, "other": other, "final": versions[1]["attachment_ref"],
            "draft": newest["attachment_ref"]}


def _outputs(client, headers, **params):
    response = client.get("/api/v1/outputs", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_chats_outputs_show_what_it_made_once_each_with_versions_inside(service, chats):
    with _client(service) as client:
        _, headers = bootstrap(client)
        page = _outputs(client, headers, conversation_id=chats["film"])

    made = {item["name"]: item for item in page["items"]}
    assert list(made) == ["Birthday montage", "cake-on-a-table", "video"]
    film = made["Birthday montage"]
    # The presented final shows, never the newer draft; every version is inside, newest first.
    assert (film["ref"], film["role"], film["version"], film["file_name"]) == (
        chats["final"], "final", 2, "Birthday montage.mp4")
    assert [(v["version"], v["role"]) for v in film["versions"]] == [(3, "draft"), (2, "final"), (1, "draft")]
    assert film["versions"][0]["ref"] == chats["draft"]
    assert (film["kind"], film["version_count"], film["working_count"]) == ("video", 3, 1)
    # A clip kept before roles was made by AI: it is final.
    assert (made["video"]["role"], made["video"]["file_name"]) == ("final", "generated-video.mp4")
    assert page["counts"] == {"made": 3, "uploads": 1, "working": 5}


def test_working_files_and_the_persons_own_files_are_listed_only_when_asked(service, chats):
    with _client(service) as client:
        _, headers = bootstrap(client)
        working = _outputs(client, headers, conversation_id=chats["film"], show="working")
        uploads = _outputs(client, headers, conversation_id=chats["film"], show="uploads")

    assert sorted(item["file_name"] for item in working["items"]) == [
        "Birthday montage review.jpg", "generated-recording.mp4", "generated-signup-sheet.jpg", "signup-01.jpg",
        "voice-1.wav"]
    assert {item["role"] for item in working["items"]} == {"working"}
    [upload] = uploads["items"]
    # Any kind of file, its own name kept as written.
    assert (upload["file_name"], upload["kind"], upload["role"]) == ("Party guests £480 café.pdf", "document",
                                                                      "upload")


def test_library_media_lists_every_chats_outputs_but_an_incognito_ones_by_words_and_kind(service, chats):
    from row_bot import threads

    with _client(service) as client:
        _, headers = bootstrap(client)
        every = _outputs(client, headers)
        assert [(item["name"], item["conversation_title"]) for item in every["items"]] == [
            ("logo-on-white", "Logo ideas"), ("Birthday montage", "Sam's birthday"),
            ("cake-on-a-table", "Sam's birthday"), ("video", "Sam's birthday")]
        assert every["counts"] is None
        assert [item["name"] for item in _outputs(client, headers, kind="image")["items"]] == [
            "logo-on-white", "cake-on-a-table"]
        assert [item["name"] for item in _outputs(client, headers, query="birthday montage")["items"]] == [
            "Birthday montage"]
        assert [item["file_name"] for item in _outputs(client, headers, show="uploads")["items"]] == [
            "Party guests £480 café.pdf"]

        threads.set_thread_incognito(chats["film"], True)
        assert [item["name"] for item in _outputs(client, headers)["items"]] == ["logo-on-white"]
        # The incognito chat's own pane still shows its outputs.
        assert len(_outputs(client, headers, conversation_id=chats["film"])["items"]) == 3


def test_the_outputs_list_refuses_an_unknown_chat_or_filter(service, chats):
    with _client(service) as client:
        _, headers = bootstrap(client)
        assert client.get("/api/v1/outputs", params={"conversation_id": "no-such-chat"},
                          headers=headers).status_code == 404
        for params in ({"show": "everything"}, {"kind": "spreadsheet"}, {"before": "not-a-page"}):
            assert client.get("/api/v1/outputs", params=params, headers=headers).status_code == 422
        assert client.get("/api/v1/outputs").status_code in {401, 403}


def test_library_media_reads_a_page_at_a_time_and_the_next_page_follows_on(service, chats, monkeypatch):
    from row_bot import media_library

    monkeypatch.setattr(media_library, "PAGE", 2)
    with _client(service) as client:
        _, headers = bootstrap(client)
        first = _outputs(client, headers)
        assert [item["name"] for item in first["items"]] == ["logo-on-white", "Birthday montage"]
        second = _outputs(client, headers, before=first["next"])
        assert [item["name"] for item in second["items"]] == ["cake-on-a-table", "video"]
        assert second["next"] is None
        # One chat's pane comes whole.
        assert len(_outputs(client, headers, conversation_id=chats["film"])["items"]) == 3
