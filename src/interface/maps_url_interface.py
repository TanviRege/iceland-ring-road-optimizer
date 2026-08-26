"""
Dynamic Google Maps URL input for the Iceland Ring Road Optimizer.

Replaces a *hardcoded* Google Maps directions URL with a value the user
supplies at runtime through a Streamlit-like interface that adapts to the
current runtime:

| Runtime                | Input widget                                  |
|------------------------|-----------------------------------------------|
| `streamlit run app.py` | st.text_input (reactive rerun)                |
| Jupyter / Colab kernel | input() prompt (linear, always works)        |
| Jupyter + Streamlit    | ipywidgets Text + Button (callback mode)      |
| Plain Python / CLI     | input() prompt                                  |

The approach splits three concerns so it is robust:
  1. detect_input_backend()          -> which widget to show
  2. acquire_google_maps_url(...)    -> get the URL
  3. validate_google_maps_url(url)   -> is it usable?
  4. parse_google_maps_url(url)      -> origin/dest/waypoints

The sample URL lives here only as a *fallback default* (env-overridable via
DEFAULT_MAPS_URL) so notebooks/apps never hardcode a live link.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus, urlsplit

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except Exception:  # pragma: no cover - dotenv is optional at import time
    pass

# --- Default route (fallback only) ------------------------------------------
DEFAULT_MAPS_URL: str = os.environ.get("DEFAULT_MAPS_URL", "")

# A Google Maps directions URL always contains "/maps/dir/".
_URL_DIR_RE = re.compile(r"/maps/dir/", re.IGNORECASE)
# After the places, Google appends either a viewport ("/@") or a "data=" block.
_SPLIT_RE = re.compile(r"/(?:@|data=)", re.IGNORECASE)

# Google's "Share -> Copy link" button often copies a SHORT redirect link
# (e.g. https://maps.app.goo.gl/xyz) rather than the long /maps/dir/... URL.
# These markers let us detect that form and expand it before validation/parsing.
_GOOGLE_SHORT_LINK_MARKERS = ("goo.gl", "maps.app.goo.gl", "g.co")
# Per-session cache so validate() -> parse() only follows the redirect once.
_resolve_cache: Dict[str, str] = {}


def _resolve_google_maps_link(url: str) -> str:
    """Expand a Google Maps short/redirect link into its final destination URL.

    Notes
    -----
    * Short share links (maps.app.goo.gl/...) are HTTP 3xx redirects; we follow
      them and return the final long ``/maps/dir/...`` URL.
    * Non-short URLs are returned unchanged (no network call).
    * The result is cached for the session so repeated calls don't re-fetch.
    """
    if url in _resolve_cache:
        return _resolve_cache[url]

    result = url
    if any(marker in url.lower() for marker in _GOOGLE_SHORT_LINK_MARKERS):
        try:
            # allow_redirects=True follows the short-hand chain; final URL is in
            # ``response.url``.
            response = requests.get(url, allow_redirects=True, timeout=15)
            if response and response.url:
                result = response.url
        except Exception:
            # Any failure (offline / blocked) -> leave unchanged; validation will
            # then report it as "not a Google Maps link", which is accurate.
            result = url
    _resolve_cache[url] = result
    return result


def _parse_query_directions_url(url: str) -> Optional[Dict[str, Any]]:
    """Extract a route from Google Maps' *query-parameter* directions format.

    Modern "Share -> Copy link" URLs (especially short links like
    ``maps.app.goo.gl/...`` after following the redirect) expand to::

        https://www.google.com/maps?saddr=<start>&daddr=<stop>&daddr=<dest>&dirflg=dt

    instead of the older ``/maps/dir/Origin/Dest/...`` path form. This helper
    pulls the origin from ``saddr`` and the destination from the *last* ``daddr``
    (any earlier ``daddr`` values become intermediate waypoints). Returns None
    if the URL is not in this form.
    """
    pairs: Dict[str, List[str]] = {}
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        pairs.setdefault(key, []).append(value)

    saddr = pairs.get("saddr") or []
    daddr = pairs.get("daddr") or []
    if not saddr or not daddr:
        return None

    return {
        "origin": saddr[0],
        "destination": daddr[-1],
        "waypoints": list(daddr[:-1]),
    }


def detect_input_backend() -> str:
    """Return the best input backend for the current runtime.

    Order matters:
      * ``streamlit`` only wins when a real Streamlit server runs
        (``st.runtime.exists()``). A bare ``import streamlit`` is NOT enough --
        in a normal Jupyter kernel the package imports fine but there is no
        runtime, which is the trap the original notebook fell into.
      * ``ipywidgets`` is used for notebooks only when a live kernel is
        detected AND ipywidgets is installed.
      * everything else degrades to a stdin ``input()`` prompt.
    """
    try:
        import streamlit as st

        if st.runtime.exists():
            return "streamlit"
    except Exception:
        pass

    try:
        import ipywidgets  # noqa: F401

        from IPython import get_ipython

        if get_ipython() is not None:
            return "ipywidgets"
    except Exception:
        pass

    return "cli"


def validate_google_maps_url(url: Optional[str]) -> Tuple[bool, str]:
    """Cheap structural check. Returns ``(is_valid, reason)``.

    Runs *before* the API call so the user gets immediate, helpful feedback
    instead of an opaque Directions-API error.
    """
    if not url or not url.strip():
        return False, "No URL was provided."

    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    # Accept short Google Maps share links (maps.app.goo.gl/...) by resolving
    # them to the full directions URL before the structural checks below.
    raw = _resolve_google_maps_link(raw)

    if "google.com/maps" not in raw:
        return False, (
            "URL does not look like a Google Maps link. "
            "Open maps.google.com, build a route, and paste the share link."
        )

    # A valid directions URL is either the classic "/maps/dir/..." path form,
    # or the modern query form (?saddr=...&daddr=...) that share links expand to.
    is_query_dirs = _parse_query_directions_url(raw) is not None
    if not (_URL_DIR_RE.search(raw) or is_query_dirs):
        return False, (
            "This URL is not a *directions* link. It should be a Google Maps "
            "directions URL (e.g. a 'Share -> Copy link' short link). Build a "
            "route on maps.google.com, then use the 'Share' button."
        )

    # At least origin + destination must be parseable.
    info = parse_google_maps_url(raw)
    if info is None or len(info["raw_places"]) < 2:
        return False, (
            "Could not find origin & destination in the URL. The link may use "
            "place-IDs only -- try re-sharing the route from maps.google.com so "
            "the place names appear in the link."
        )

    return True, "ok"


def parse_google_maps_url(url: Optional[str]) -> Optional[Dict[str, object]]:
    """Extract origin / destination / waypoints from a Google Maps directions URL.

    Handles the common share-link shapes::

        https://www.google.com/maps/dir/A/B/C/@lat,lng,zoom/data=...
        https://maps.google.com/maps/dir/A/B/C?entry=ttu
        https://www.google.com/maps/dir/A/B/%20%20/data=...   (%20 encoded)
        https://www.google.com/maps?saddr=<start>&daddr=<dest>  (query form)

    Short links (e.g. ``maps.app.goo.gl/...``) are resolved to their long form
    first, so both the path form and the modern query form are supported.

    Returns a dict with keys ``origin``, ``destination``, ``waypoints``
    (list or ``None``), ``raw_places`` and ``source_url``; or ``None`` if the
    URL does not yield at least an origin + destination.
    """
    if not url or not url.strip():
        return None

    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    # Accept short Google links here too, so parse can be called directly
    # (independent of validate) and still expand maps.app.goo.gl/... URLs.
    raw = _resolve_google_maps_link(raw)

    # Short links are expanded above; handle either directions format:
    #   A) classic path form   /maps/dir/Origin/Dest/@...
    #   B) modern query form   ?saddr=<start>&daddr=<stop>&daddr=<dest>
    if _URL_DIR_RE.search(raw):
        # Everything after "/maps/dir/".
        after_dir = _URL_DIR_RE.split(raw, maxsplit=1)[1]
        # Cut at the viewport "/@" or the "data=" analytics block, whichever comes first.
        after_dir = _SPLIT_RE.split(after_dir, maxsplit=1)[0]

        segments = [seg for seg in after_dir.split("/") if seg.strip()]

        # Decode percent-encoding AND '+' -> space in one step (unquote_plus), then
        # strip stray leading/trailing whitespace that a leading '+' can produce.
        places = [unquote_plus(seg).strip() for seg in segments]
        places = [p for p in places if p]
        if len(places) < 2:
            return None

        waypoints = places[1:-1] if places[1:-1] else None
        return {
            "origin": places[0],
            "destination": places[-1],
            "waypoints": waypoints,
            "raw_places": places,
            "source_url": url,
        }

    # Modern query-parameter directions (what short share links expand to).
    qdir = _parse_query_directions_url(raw)
    if qdir is None:
        return None

    origin = unquote_plus(qdir["origin"]).strip()
    destination = unquote_plus(qdir["destination"]).strip()
    waypoints = [unquote_plus(w).strip() for w in qdir["waypoints"]]
    waypoints = [w for w in waypoints if w]

    places = [origin, *waypoints, destination]
    return {
        "origin": origin,
        "destination": destination,
        "waypoints": waypoints or None,
        "raw_places": places,
        "source_url": url,
    }


def acquire_google_maps_url(
    label: str = "\U0001f4cd Paste a Google Maps directions URL",
    default: Optional[str] = None,
    help_text: str = (
        "Open maps.google.com \u2192 Directions \u2192 build a route \u2192 "
        "Share \u2192 Copy link \u2192 paste here."
    ),
    text_input_key: str = "gmaps_url",
    submit_callback: Optional[Callable[[str], None]] = None,
    backend: Optional[str] = None,
) -> Optional[str]:
    """Present a text input for a Google Maps directions URL.

    Parameters
    ----------
    backend:
        Force a backend. If ``None`` it is auto-detected.
    submit_callback:
        If supplied, the input becomes *reactive*: instead of returning a
        string immediately it fires ``submit_callback(url)`` when the user
        submits (Streamlit forms / Jupyter button). If ``None`` the function
        returns the URL string immediately (``st.text_input`` under Streamlit,
        ``input()`` otherwise).

    Returns the URL string (no callback) or ``None`` (callback mode).
    """
    default = default or DEFAULT_MAPS_URL
    backend = backend or detect_input_backend()

    # -- Streamlit: the only backend with a true, reactive text input. ----------
    if backend == "streamlit":
        import streamlit as st

        if submit_callback is None:
            return st.text_input(label, value=default, help=help_text, key=text_input_key)
        with st.form(key=text_input_key + "_form"):
            url = st.text_input(label, value=default, help=help_text, key=text_input_key)
            if st.form_submit_button("\U0001f680 Resolve route"):
                submit_callback(url.strip())
        return None

    # -- Jupyter widgets (callback mode only -- keeps the kernel responsive). ---
    if backend == "ipywidgets" and submit_callback is not None:
        import ipywidgets as widgets
        from IPython.display import display

        url_box = widgets.Text(
            value=default,
            placeholder=help_text,
            description=label.split()[0] + ":",
            layout=widgets.Layout(width="100%"),
        )
        url_box.style.description_width = "initial"
        go = widgets.Button(description="\U0001f680 Resolve route", button_style="primary")
        out = widgets.Output()

        def _on_submit(_):
            out.clear_output()
            with out:
                submit_callback(url_box.value.strip())

        go.on_click(_on_submit)
        display(widgets.VBox([url_box, go, out]))
        return None

    # -- Fallback: stdin prompt. Works in Jupyter (linear), CLI, etc. This is
    #     what a plain notebook uses. -------------------------------
    prompt = f"{label} (press Enter to use default): "
    try:
        url = input(prompt).strip() or default
    except EOFError:
        url = default
    if submit_callback is not None:
        submit_callback(url)
        return None
    return url


__all__ = [
    "DEFAULT_MAPS_URL",
    "detect_input_backend",
    "validate_google_maps_url",
    "parse_google_maps_url",
    "acquire_google_maps_url",
]

