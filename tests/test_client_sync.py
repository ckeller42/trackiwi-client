import json

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import Client

PAGE_1 = b"1,7,1758000000,120,31.0,-41.0,12,0.0,0,0,-71,9,98,4120\n"
PAGE_2 = b"2,7,1758000060,120,31.1,-41.1,12,0.0,0,0,-71,9,98,4120\n"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def client(*responses):
    return Client(
        api_base="https://api.example.invalid",
        token="tok",
        user_id=42,
        opener=FakeOpener(*responses),
    )


def test_sync_pages_until_empty():
    c = client(
        FakeResponse(PAGE_1, headers={"trackiwi-position-count": "2"}),
        FakeResponse(PAGE_2),
        FakeResponse(b""),
    )
    batches = list(c.sync())
    assert [rows[0][0] for rows, _, _ in batches] == [1, 2]


def test_first_call_requests_initial_sync_then_offsets():
    opener = FakeOpener(FakeResponse(PAGE_1), FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync())
    assert json.loads(opener.calls[0].data) == {"initial_sync": True}
    assert json.loads(opener.calls[1].data) == {"offset": 1}


def test_resuming_from_an_offset_skips_initial_sync():
    opener = FakeOpener(FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync(offset=99))
    assert json.loads(opener.calls[0].data) == {"offset": 99}


def test_total_comes_from_the_count_header():
    c = client(FakeResponse(PAGE_1, headers={"trackiwi-position-count": "7"}), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total == 7


def test_total_is_none_without_the_header():
    c = client(FakeResponse(PAGE_1), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total is None


def test_malformed_rows_are_counted_not_fatal():
    c = client(FakeResponse(PAGE_1 + b"broken,row\n"), FakeResponse(b""))
    _, skipped, _ = next(iter(c.sync()))
    assert skipped == 1


def test_sync_posts_to_the_api_base():
    opener = FakeOpener(FakeResponse(b""))
    client = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(client.sync())
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/trackers/sync"


def test_trackers_accepts_a_bare_list():
    c = client(FakeResponse(b'[{"id":7,"name":"Bus"}]'))
    assert c.trackers()[0]["name"] == "Bus"


def test_trackers_accepts_a_data_envelope():
    c = client(FakeResponse(b'{"data":[{"id":7,"name":"Bus"}]}'))
    assert c.trackers()[0]["id"] == 7
