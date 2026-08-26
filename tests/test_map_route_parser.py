"""
Tests for the dynamic Google Maps URL helper layer.

Covers URL parsing variants, validation, backend detection, and the
``acquire_google_maps_url`` streamlit/cli branches (using an in-memory fake
Streamlit so the test runs without a live server).
"""
from __future__ import annotations

import sys
import types

from src.interface.maps_url_interface import (
    DEFAULT_MAPS_URL,
    parse_google_maps_url,
    validate_google_maps_url,
)

SAMPLE_URL = (
    "https://www.google.com/maps/dir/Reykjavik,+Iceland/Skaftafell,"
    "+785+Skaftafell,+Iceland/@63.7581615,-22.138936,7z/data=!3m1!4b1"
    "!4m14!4m13!1m5!1m1!1s0x48d674b9eedcedc3:0xec912ca230d26071!2m2!1d"
    "-21.9407552!2d64.1469868!1m5!1m1!1s0x48d035dd195fd15b:0x7aeae20830"
    "cc34d0!2m2!1d-16.9751755!2d64.070414!3e0?entry=ttu"
)
WP_URL = (
    "https://www.google.com/maps/dir/Akureyri,+Iceland/"
    "Egilsstadir,+Iceland/Hofn,+Iceland/@65.0,-16.0,7z/data=!3m1!4b1!4m"
    "14!4m13!3e0?entry=ttu"
)


# --------------------------------------------------------------------------- #
# parse_google_maps_url
# --------------------------------------------------------------------------- #
def test_parse_sample_ring_road():
    info = parse_google_maps_url(SAMPLE_URL)
    assert info is not None
    assert info["origin"] == "Reykjavik, Iceland"
    assert info["destination"] == "Skaftafell, 785 Skaftafell, Iceland"
    assert info["waypoints"] is None
    assert info["raw_places"] == [info["origin"], info["destination"]]
    assert info["source_url"] == SAMPLE_URL


def test_parse_with_waypoints():
    info = parse_google_maps_url(WP_URL)
    assert info is not None
    assert info["origin"] == "Akureyri, Iceland"
    assert info["destination"] == "Hofn, Iceland"
    assert info["waypoints"] == ["Egilsstadir, Iceland"]


def test_parse_strips_scheme_when_missing():
    info = parse_google_maps_url("www.google.com/maps/dir/Reykjavik/Skaftafell")
    assert info is not None
    assert info["origin"] == "Reykjavik"
    assert info["destination"] == "Skaftafell"


def test_parse_decodes_percent_and_plus():
    url = "https://www.google.com/maps/dir/Reykjavik%20City/+Skaftafell/@1,1,7z/data=x"
    info = parse_google_maps_url(url)
    assert info is not None
    assert info["origin"] == "Reykjavik City"
    assert info["destination"] == "Skaftafell"


def test_parse_no_dir_segment_returns_none():
    assert parse_google_maps_url("https://www.google.com/maps/place/Reykjavik") is None


def test_parse_invalid_returns_none():
    assert parse_google_maps_url("not a url") is None
    assert parse_google_maps_url("") is None
    assert parse_google_maps_url(None) is None


def test_parse_too_few_places_returns_none():
    url = "https://www.google.com/maps/dir/Reykjavik/@1,1,7z/data=x"
    assert parse_google_maps_url(url) is None


# --------------------------------------------------------------------------- #
# validate_google_maps_url
# --------------------------------------------------------------------------- #
def test_validate_good_url():
    ok, reason = validate_google_maps_url(SAMPLE_URL)
    assert ok is True
    assert reason == "ok"


def test_validate_empty():
    ok, reason = validate_google_maps_url("")
    assert ok is False
    assert "No URL" in reason


def test_validate_non_google():
    ok, reason = validate_google_maps_url("https://example.com/foo/bar")
    assert ok is False
    assert "Google Maps" in reason


def test_validate_place_url_no_dir():
    ok, reason = validate_google_maps_url("https://www.google.com/maps/place/Reykjavik")
    assert ok is False
    assert "directions" in reason.lower()


def test_default_url_is_empty():
    # DEFAULT_MAPS_URL is now empty string (no default route)
    assert DEFAULT_MAPS_URL == ""
    ok, reason = validate_google_maps_url(DEFAULT_MAPS_URL)
    assert ok is False
    assert "No URL" in reason


# --------------------------------------------------------------------------- #
# short Google Maps share links (maps.app.goo.gl/...) -> resolve then parse
# --------------------------------------------------------------------------- #
from unittest import mock

from src.interface.maps_url_interface import _resolve_cache


def _mock_redirect_response(*_args, **_kwargs):
    """Fake requests.get() that 'redirects' a short link to SAMPLE_URL."""

    class _Resp:
        url = SAMPLE_URL

    return _Resp()


def test_short_link_resolves_then_parses():
    _resolve_cache.clear()
    short = "https://maps.app.goo.gl/txqDbX"
    with mock.patch(
        "src.interface.maps_url_interface.requests.get",
        side_effect=_mock_redirect_response,
    ):
        info = parse_google_maps_url(short)
    assert info is not None
    assert info["origin"] == "Reykjavik, Iceland"
    assert info["destination"] == "Skaftafell, 785 Skaftafell, Iceland"
    # source_url keeps the original pasted link
    assert info["source_url"] == short


def test_short_link_resolves_then_validates():
    _resolve_cache.clear()
    short = "https://maps.app.goo.gl/txPqB"
    with mock.patch(
        "src.interface.maps_url_interface.requests.get",
        side_effect=_mock_redirect_response,
    ):
        ok, reason = validate_google_maps_url(short)
    assert ok is True
    assert reason == "ok"


def test_short_link_unresolvable_falls_back_to_error():
    _resolve_cache.clear()

    def _raise(*_args, **_kwargs):
        raise IOError("offline")

    with mock.patch(
        "src.interface.maps_url_interface.requests.get",
        side_effect=_raise,
    ):
        ok, reason = validate_google_maps_url("https://maps.app.goo.gl/txAbC")
    assert ok is False


# --------------------------------------------------------------------------- #
# modern query-parameter directions URL (?saddr=...&daddr=...)
# --------------------------------------------------------------------------- #
def test_parse_query_form_single_stop():
    # What a modern short share link expands to (Reykjavik -> Vik).
    url = ("https://www.google.com/maps?saddr=Reykjav%C3%ADk,+Iceland"
           "&daddr=Vik,+870,+Iceland&dirflg=dt&g_st=ic")
    info = parse_google_maps_url(url)
    assert info is not None
    assert info["origin"] == "Reykjavík, Iceland"
    assert info["destination"] == "Vik, 870, Iceland"
    assert info["waypoints"] is None
    assert info["raw_places"] == ["Reykjavík, Iceland", "Vik, 870, Iceland"]


def test_parse_query_form_with_stops():
    # Multiple daddr -> intermediate waypoints + final destination.
    url = ("https://www.google.com/maps?saddr=Reykjavik"
           "&daddr=Borgarnes&daddr=Akureyri&daddr=Vik&dirflg=dt")
    info = parse_google_maps_url(url)
    assert info is not None
    assert info["origin"] == "Reykjavik"
    assert info["destination"] == "Vik"
    assert info["waypoints"] == ["Borgarnes", "Akureyri"]


def test_parse_query_form_missing_params_returns_none():
    url = "https://www.google.com/maps?q=Reykjavik&z=8"
    assert parse_google_maps_url(url) is None


def test_validate_query_form():
    url = ("https://www.google.com/maps?saddr=Reykjav%C3%ADk,+Iceland"
           "&daddr=Vik,+870,+Iceland&dirflg=dt")
    ok, reason = validate_google_maps_url(url)
    assert ok is True
    assert reason == "ok"


# --------------------------------------------------------------------------- #
# detect_input_backend
# --------------------------------------------------------------------------- #
from src.interface.maps_url_interface import detect_input_backend, acquire_google_maps_url


def test_detect_cli_in_plain_pytest():
    assert detect_input_backend() == "cli"


def test_detect_streamlit_via_fake_module(monkeypatch):
    import IPython  # noqa: F401

    fake = types.ModuleType("streamlit")

    class _Runtime:
        @staticmethod
        def exists():
            return True

    fake.runtime = _Runtime()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    assert detect_input_backend() == "streamlit"


def test_detect_ipywidgets_via_stubbed_kernel(monkeypatch):
    import IPython

    monkeypatch.setattr(IPython, "get_ipython", lambda: object())
    assert detect_input_backend() == "ipywidgets"


# --------------------------------------------------------------------------- #
# acquire_google_maps_url (streamlit / cli branches)
# --------------------------------------------------------------------------- #
def test_acquire_returns_text_input_value_in_streamlit(monkeypatch):
    fake = types.ModuleType("streamlit")

    class _Runtime:
        @staticmethod
        def exists():
            return True

    fake.runtime = _Runtime()
    captured = {}

    def fake_text_input(label, value=None, help=None, key=None):
        captured["value"] = value
        captured["key"] = key
        return "https://www.google.com/maps/dir/A/B"

    fake.text_input = fake_text_input
    monkeypatch.setitem(sys.modules, "streamlit", fake)

    url = acquire_google_maps_url(backend="streamlit")
    assert url == "https://www.google.com/maps/dir/A/B"
    assert captured["value"] == DEFAULT_MAPS_URL
    assert captured["key"] == "gmaps_url"


def test_acquire_cli_falls_back_to_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "https://www.google.com/maps/dir/X/Y")
    url = acquire_google_maps_url(backend="cli")
    assert url == "https://www.google.com/maps/dir/X/Y"


def test_acquire_cli_uses_default_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    url = acquire_google_maps_url(
        backend="cli", default="https://www.google.com/maps/dir/A/B"
    )
    assert url == "https://www.google.com/maps/dir/A/B"
