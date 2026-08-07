"""
Iceland Ring Road Optimizer - Streamlit interface app.

Run with:
    streamlit run app.py

This is the "Streamlit-like interface" front-end. It replaces the notebook's
hardcoded Google Maps URL with a reactive `st.text_input` that the user fills
in at runtime, then drives the same route -> directions -> weather-station ->
weather pipeline used by the notebook.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import streamlit as st

from src.interface.directions import get_directions
from src.interface.maps_url_interface import (
    DEFAULT_MAPS_URL,
    acquire_google_maps_url,
    parse_google_maps_url,
    validate_google_maps_url,
)

st.set_page_config(
    page_title="Iceland Ring Road Optimizer",
    page_icon="🇮🇸",
    layout="wide",
)

# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
st.sidebar.header("🇮🇸 Iceland Ring Road Optimizer")
st.sidebar.caption(
    "Paste a Google Maps **directions** URL and we'll sample weather stations "
    "along the route."
)
if "GOOGLE_MAPS_API_KEY" not in os.environ:
    try:
        if not st.secrets.get("GOOGLE_MAPS_API_KEY"):
            st.sidebar.warning(
                "Put your `GOOGLE_MAPS_API_KEY` in `.env` (project root) or `.streamlit/secrets.toml` before fetching directions."
            )
    except Exception:
        st.sidebar.warning(
            "Put your `GOOGLE_MAPS_API_KEY` in `.env` (project root) or `.streamlit/secrets.toml` before fetching directions."
        )

# --------------------------------------------------------------------------- #
# 1. URL input (reactive Streamlit text input)
# --------------------------------------------------------------------------- #
st.markdown("## 📍 Route input")
long_url = acquire_google_maps_url(
    label="Google Maps directions URL",
    default=DEFAULT_MAPS_URL,
    help_text="Open maps.google.com → Directions → build a route → Share → Copy link → paste here.",
)
st.caption(
    "Don't have one? Use the sample Ring Road route already filled in, or paste "
    "any route you build on Google Maps."
)

# --------------------------------------------------------------------------- #
# 2. Validate + parse  (give feedback *before* hitting the Directions API)
# --------------------------------------------------------------------------- #
if not long_url:
    st.info("Paste a Google Maps directions URL to get started.")
    st.stop()

ok, reason = validate_google_maps_url(long_url)
if not ok:
    st.error(f"❌ {reason}")
    st.stop()

route_info = parse_google_maps_url(long_url)
if route_info is None:
    st.error("❌ Could not extract origin/destination from the URL.")
    st.stop()

st.success("✅ Valid Google Maps directions URL")
with st.expander("Parsed route places"):
    st.json(route_info)

st.markdown("---")

# --------------------------------------------------------------------------- #
# 3. Fetch directions
# --------------------------------------------------------------------------- #
if "directions" not in st.session_state:
    st.session_state.directions = None

if st.session_state.directions is None:
    if st.button("🚗 Fetch directions", type="primary"):
        with st.spinner("Calling Google Directions API…"):
            directions = get_directions(
                origin=route_info["origin"],
                destination=route_info["destination"],
                waypoints=route_info["waypoints"],
            )
        st.session_state.directions = directions
        st.session_state.route_info = route_info
        if not directions:
            st.error("❌ Directions API call failed. Check your GOOGLE_MAPS_API_KEY and URL.")

directions = st.session_state.get("directions")
if not directions:
    st.stop()

# --------------------------------------------------------------------------- #
# 4. Route summary metrics
# --------------------------------------------------------------------------- #
st.markdown("## 🗺️ Route summary")
col1, col2, col3 = st.columns(3)
col1.metric("Total distance", directions["total_distance_text"])
col2.metric("Estimated drive time", directions["total_duration_text"])
col3.metric(
    "Stops (incl. waypoints)",
    len(route_info["raw_places"]),
)

# Simple route map from sampled leg points.
try:
    points = [s["start_location"] for leg in directions["legs"] for s in leg["steps"]]
    if points:
        pts = pd.DataFrame(points)
        fig = px.line_mapbox(
            pts, lat="lat", lon="lng", zoom=6, height=420,
            title="Route path",
        )
        fig.update_layout(mapbox_style="open-street-map", margin=dict(l=0, r=0, t=30, b=0))
        fig.update_layout(mapbox_accesstoken=os.environ.get("MAPBOX_TOKEN", ""))
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:  # pragma: no cover
    st.caption(f"Map preview unavailable: {exc}")

# --------------------------------------------------------------------------- #
# 5. Weather stations along the route (Vedur API)
# --------------------------------------------------------------------------- #
st.markdown("---")
st.markdown("## 🌡️ Weather stations along the route")
if st.button("📡 Fetch live weather stations"):
    try:
        from src.ingestion.vedur_station_mapper import VedurRouteWeatherMapper

        mapper = VedurRouteWeatherMapper()
        matched, df_weather = mapper.get_route_weather_pipeline(directions)
        if matched:
            st.dataframe(pd.DataFrame(matched), use_container_width=True)
        if not df_weather.empty:
            st.subheader("Latest observations")
            st.dataframe(df_weather, use_container_width=True)
        else:
            st.info("No weather observations returned for matched stations.")
    except Exception as exc:  # pragma: no cover
        st.error(f"Weather fetch failed: {exc}")
