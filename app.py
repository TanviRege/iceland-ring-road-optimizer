"""
Iceland Ring Road Optimizer - Streamlit interface app.

Run with either:
    streamlit run app.py
    python app.py          # auto-launches streamlit

This is the "Streamlit-like interface" front-end. It replaces the notebook's
hardcoded Google Maps URL with a reactive `st.text_input` that the user fills
in at runtime, then drives the same route -> directions -> weather-station ->
weather pipeline used by the notebook.
"""
from __future__ import annotations

import os
import sys


def _ensure_streamlit_runtime():
    """If this file was invoked with `python app.py`, re-launch it via Streamlit."""
    try:
        import streamlit as st
        if st.runtime.exists():
            return  # already inside a Streamlit server – carry on
    except Exception:
        pass

    # We're NOT inside a Streamlit runtime – relaunch properly.
    import subprocess
    print("\n🚀 Launching Streamlit web app...\n")
    sys.exit(
        subprocess.call(
            [sys.executable, "-m", "streamlit", "run", __file__,
             "--server.headless=true", "--browser.gatherUsageStats=false"],
        )
    )


_ensure_streamlit_runtime()

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from src.interface.directions import get_directions, geocode_place
from src.interface.maps_url_interface import (
    DEFAULT_MAPS_URL,
    acquire_google_maps_url,
    parse_google_maps_url,
    validate_google_maps_url,
)
from src.ingestion.vedur_station_mapper import VedurRouteWeatherMapper
from src.ingestion.route_cache import get_route_data, list_cached_routes, _generate_route_key, clear_route_cache

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
# Sidebar: Suggested Ring Road stops
# --------------------------------------------------------------------------- #
# Major towns along Iceland's Route 1 (Ring Road), clockwise from Reykjavík
RING_ROAD_TOWNS = [
    "Reykjavík, Iceland",
    "Borgarnes, Iceland",
    "Bifröst, Iceland",
    "Laugarbakki, Iceland",
    "Blönduós, Iceland",
    "Varmahlíð, Iceland",
    "Akureyri, Iceland",
    "Goðafoss, Iceland",
    "Mývatn, Iceland",
    "Egilsstaðir, Iceland",
    "Höfn, Iceland",
    "Kirkjubæjarklaustur, Iceland",
    "Vík í Mýrdal, Iceland",
    "Selfoss, Iceland",
    "Reykjavík, Iceland",  # Full loop
]

def _haversine_km(lat1, lon1, lat2, lon2):
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

@st.cache_data(ttl=3600)
def _geocode_towns(towns, api_key):
    """Batch geocode towns for distance calculations."""
    results = {}
    for town in towns:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": town, "key": api_key}
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data["status"] == "OK" and data["results"]:
                loc = data["results"][0]["geometry"]["location"]
                results[town] = {"lat": loc["lat"], "lng": loc["lng"]}
        except Exception:
            pass
    return results

def get_suggested_ring_road_stops(origin, destination, api_key):
    """Return Ring Road towns between origin and destination in driving order."""
    if not api_key or api_key == "your_google_maps_api_key_here":
        return []
    
    # Geocode origin, destination, and all Ring Road towns
    geo_origin = _geocode_towns([origin], api_key).get(origin)
    geo_dest = _geocode_towns([destination], api_key).get(destination)
    geo_towns = _geocode_towns(RING_ROAD_TOWNS, api_key)
    
    if not geo_origin or not geo_dest:
        return []
    
    # Find closest Ring Road town to origin and destination
    def find_closest(geo_point):
        min_dist = float('inf')
        closest_idx = 0
        for i, town in enumerate(RING_ROAD_TOWNS):
            if town in geo_towns:
                d = _haversine_km(
                    geo_point["lat"], geo_point["lng"],
                    geo_towns[town]["lat"], geo_towns[town]["lng"]
                )
                if d < min_dist:
                    min_dist = d
                    closest_idx = i
        return closest_idx
    
    origin_idx = find_closest(geo_origin)
    dest_idx = find_closest(geo_dest)
    
    # Determine shorter direction around the ring
    ring_len = len(RING_ROAD_TOWNS) - 1  # Exclude duplicate Reykjavík at end
    
    # Clockwise distance (number of towns)
    if dest_idx >= origin_idx:
        cw_towns = RING_ROAD_TOWNS[origin_idx + 1:dest_idx]
    else:
        cw_towns = RING_ROAD_TOWNS[origin_idx + 1:] + RING_ROAD_TOWNS[:dest_idx]
    
    # Counter-clockwise
    if origin_idx >= dest_idx:
        ccw_towns = RING_ROAD_TOWNS[dest_idx + 1:origin_idx]
    else:
        ccw_towns = RING_ROAD_TOWNS[dest_idx + 1:] + RING_ROAD_TOWNS[:origin_idx]
    ccw_towns = list(reversed(ccw_towns))
    
    # Return shorter path
    return cw_towns if len(cw_towns) <= len(ccw_towns) else ccw_towns

# Show suggested stops if we have a parsed route
if "route_info" in st.session_state and st.session_state.route_info:
    route_info = st.session_state.route_info
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if api_key and api_key != "your_google_maps_api_key_here":
        suggested = get_suggested_ring_road_stops(
            route_info["origin"], route_info["destination"], api_key
        )
        if suggested:
            st.sidebar.markdown("---")
            st.sidebar.subheader("🗺️ Suggested Ring Road stops")
            st.sidebar.caption(
                "Add these as stops in Google Maps to keep your route on Route 1. "
                "Then copy the new URL and paste it here."
            )
            stops_text = "\n".join([f"{i+1}. {town}" for i, town in enumerate(suggested)])
            st.sidebar.text_area(
                "Copy these towns:",
                value=stops_text,
                height=min(200, 30 + len(suggested) * 22),
                help="Add each as a stop in Google Maps Directions",
                key="suggested_stops"
            )

# --------------------------------------------------------------------------- #
# Sidebar: Cache Management
# --------------------------------------------------------------------------- #
st.sidebar.markdown("---")
st.sidebar.subheader("💾 Route Cache")
cached_routes = list_cached_routes()
if cached_routes:
    st.sidebar.caption(f"Found {len(cached_routes)} cached route(s)")
    for cr in cached_routes:
        with st.sidebar.expander(f"🗺️ {cr['stops'][0] if cr['stops'] else cr['route_key']} → {cr['stops'][-1] if len(cr['stops']) > 1 else '...'}", expanded=False):
            st.write(f"**Stations:** {cr['stations']}")
            st.write(f"**Fuel stops:** {cr['fuel_stops']}")
            st.write(f"**Created:** {cr['created'][:19]}")
            if st.button("🗑️ Delete", key=f"del_{cr['route_key']}"):
                clear_route_cache(cr['route_key'])
                st.rerun()
else:
    st.sidebar.caption("No cached routes yet")

if st.sidebar.button("🗑️ Clear All Cache"):
    cleared = clear_route_cache()
    st.sidebar.success(f"Cleared {cleared} cached route(s)")
    st.rerun()

# --------------------------------------------------------------------------- #
# Display settings – unit toggles
# --------------------------------------------------------------------------- #
ISK_TO_USD_RATE = 135.0  # Fallback exchange rate (1 USD ≈ 135 ISK)


@st.cache_data(ttl=600)
def _get_isk_rate():
    """Fetch the live USD→ISK exchange rate (cached for 10 min).

    Falls back to ``ISK_TO_USD_RATE`` when the API is unreachable.
    Uses open.er-api.com – no API key required.
    """
    try:
        resp = requests.get(
            "https://open.er-api.com/v6/latest/USD", timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("result") == "success" and "ISK" in data.get("rates", {}):
            return float(data["rates"]["ISK"])
    except Exception:
        pass
    return ISK_TO_USD_RATE


def _c_to_f(c):
    """Convert Celsius to Fahrenheit, preserving NaN."""
    if pd.isna(c):
        return c
    return c * 9 / 5 + 32


def _isk_to_usd(isk, rate=None):
    """Convert ISK to USD, preserving NaN.

    Uses a live exchange rate (cached) unless *rate* is provided.
    """
    if pd.isna(isk):
        return isk
    if rate is None:
        rate = _get_isk_rate()
    return isk / rate


st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    temp_f = st.toggle("🌡️ Fahrenheit", value=False)
with col2:
    currency_usd = st.toggle("💰 USD", value=False)
st.markdown("---")

temp_unit = "°F" if temp_f else "°C"
currency = "USD" if currency_usd else "ISK"


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
            # Use waypoints from user's Google Maps URL only
            user_waypoints = route_info["waypoints"] or []
            directions = get_directions(
                origin=route_info["origin"],
                destination=route_info["destination"],
                waypoints=user_waypoints,
            )
        st.session_state.directions = directions
        st.session_state.route_info = route_info
        st.session_state.user_waypoints = user_waypoints
        if not directions:
            st.error("❌ Directions API call failed. Check your GOOGLE_MAPS_API_KEY and URL.")
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

# Show waypoint info
user_waypoints = st.session_state.get("user_waypoints", [])
total_stops = len(user_waypoints) + 2  # +2 for origin + destination
col3.metric(
    "Stops (incl. waypoints)",
    total_stops,
    help=f"From your Google Maps URL: {len(route_info.get('raw_places') or [])} places"
)

# Show user waypoints in expander
if user_waypoints:
    with st.expander("🛣️ Your waypoints (from Google Maps URL)"):
        for i, wp in enumerate(user_waypoints):
            st.write(f"{i+1}. {wp}")

# Route map with origin, destination, and waypoint markers
try:
    # Collect route polyline points
    points = [s["start_location"] for leg in directions["legs"] for s in leg["steps"]]
    if points:
        pts = pd.DataFrame(points)
        
        # Build marker data: origin, destination (waypoints handled separately from user_waypoints)
        marker_data = []
        # Origin
        marker_data.append({
            "lat": directions["legs"][0]["start_location"]["lat"],
            "lon": directions["legs"][0]["start_location"]["lng"],
            "name": route_info["origin"],
            "type": "origin"
        })
        # Destination
        last_leg = directions["legs"][-1]
        marker_data.append({
            "lat": last_leg["end_location"]["lat"],
            "lon": last_leg["end_location"]["lng"],
            "name": route_info["destination"],
            "type": "destination"
        })
        
        markers = pd.DataFrame(marker_data)
        
        # Create map with route line + markers using CartoDB Positron (lighter, English labels)
        fig = px.line_mapbox(
            pts, lat="lat", lon="lng", zoom=6, height=480,
            title="Route Overview",
        )
        # Hide legend for the route line
        fig.data[0].update(showlegend=False, name="Route", hoverinfo="skip")
        
        # Add origin marker (green) - use go.Scattermapbox directly to avoid duplicate legend entries
        origin_m = markers[markers["type"] == "origin"]
        if not origin_m.empty:
            fig.add_trace(go.Scattermapbox(
                lat=origin_m["lat"],
                lon=origin_m["lon"],
                mode="markers",
                marker=dict(size=16, color="#22c55e", symbol="circle"),
                name="🟢 Origin",
                showlegend=True,
                hovertext=origin_m["name"],
                hovertemplate="<b>%{hovertext}</b><br>Origin<extra></extra>",
            ))
        
        # Add waypoint markers (blue) - use go.Scattermapbox directly
        # Use user_waypoints from session state (original waypoints from Google Maps URL)
        # because Directions API with "via:" waypoints doesn't create separate legs
        user_waypoints = st.session_state.get("user_waypoints", [])
        if user_waypoints:
            # Geocode waypoints to get lat/lon for map display
            wp_coords = []
            for wp in user_waypoints:
                # Try to find in route_info raw_places or geocode
                geo = geocode_place(wp)
                if geo:
                    wp_coords.append({"lat": geo["lat"], "lon": geo["lng"], "name": geo["formatted_address"]})
            
            if wp_coords:
                wp_df = pd.DataFrame(wp_coords)
                fig.add_trace(go.Scattermapbox(
                    lat=wp_df["lat"],
                    lon=wp_df["lon"],
                    mode="markers",
                    marker=dict(size=14, color="#3b82f6", symbol="circle"),
                    name="🔵 Waypoint",
                    showlegend=True,
                    hovertext=wp_df["name"],
                    hovertemplate="<b>%{hovertext}</b><br>Waypoint<extra></extra>",
                ))
        
        # Add destination marker (red) - use go.Scattermapbox directly
        dest_m = markers[markers["type"] == "destination"]
        if not dest_m.empty:
            fig.add_trace(go.Scattermapbox(
                lat=dest_m["lat"],
                lon=dest_m["lon"],
                mode="markers",
                marker=dict(size=16, color="#ef4444", symbol="circle"),
                name="🔴 Destination",
                showlegend=True,
                hovertext=dest_m["name"],
                hovertemplate="<b>%{hovertext}</b><br>Destination<extra></extra>",
            ))
        
        # Add "Iceland" label at center of country
        iceland_label = pd.DataFrame({
            "lat": [64.96],
            "lon": [-19.02],
            "text": ["<b>Iceland</b>"]
        })
        fig.add_trace(px.scatter_mapbox(
            iceland_label, lat="lat", lon="lon",
            text="text",
            color_discrete_sequence=["rgba(0,0,0,0)"],
            zoom=6,
        ).data[0])
        fig.data[-1].update(
            mode="text",
            textfont=dict(size=20, color="#1f2937", family="Arial Black"),
            textposition="middle center",
            showlegend=False,
            hoverinfo="skip",
        )
        
        fig.update_layout(
            mapbox_style="carto-positron",  # Lighter style with English labels
            margin=dict(l=0, r=0, t=50, b=0),
            legend=dict(
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="right", x=1,
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="#9ca3af",
                borderwidth=1,
                font=dict(color="#1f2937", size=12),  # Dark text for readability
            ),
        )
        mapbox_token = os.environ.get("MAPBOX_TOKEN", "")
        if mapbox_token:
            fig.update_layout(mapbox_accesstoken=mapbox_token)
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:  # pragma: no cover
    st.caption(f"Map preview unavailable: {exc}")

# --------------------------------------------------------------------------- #
# 5. Route-ordered station mapping (Vedur API)
# --------------------------------------------------------------------------- #
# Run the Vedur pipeline to get stations in actual route order
with st.spinner("🔄 Mapping weather stations along route..."):
    mapper = VedurRouteWeatherMapper()
    matched_stations, _ = mapper.get_route_weather_pipeline(directions)

# Build route-order index: station_name -> route position
route_order = {s["station_name"]: i for i, s in enumerate(matched_stations)}

# --------------------------------------------------------------------------- #
# 6. Route Analytics Dashboard (DuckDB)
# --------------------------------------------------------------------------- #
st.markdown("---")
st.markdown("## 📊 Route Analytics Dashboard")
st.caption(
    "Weather conditions and fuel economics from live APIs with route-based caching. "
    "Data sourced from Icelandic Met Office (Veður) and Gasvaktin fuel price feeds."
)

# ---- Get or create cached route data (live + cached) ---------------------
with st.spinner("📡 Loading route data (live weather + fuel prices)..."):
    try:
        df_wx, df_fuel = get_route_data(
            origin=route_info["origin"],
            destination=route_info["destination"],
            waypoints=route_info["waypoints"],
            api_key=os.environ.get("GOOGLE_MAPS_API_KEY"),
        )
    except Exception as exc:
        st.error(f"❌ Failed to load route data: {exc}")
        st.stop()

# ---- Sort by route order (from VedurRouteWeatherMapper) ------------------
df_wx["route_order"] = df_wx["station_name"].map(route_order)
df_wx = df_wx.sort_values("route_order", na_position="last").drop(columns=["route_order"]).reset_index(drop=True)

df_fuel["route_order"] = df_fuel["station_name"].map(route_order)
df_fuel = df_fuel.sort_values("route_order", na_position="last").drop(columns=["route_order"]).reset_index(drop=True)

# ---- Create display-ready copies (with unit conversions) -----------
df_wx_display = df_wx.copy()
if temp_unit == "°F":
    temp_cols = [c for c in df_wx_display.columns if c.endswith("_c")]
    for col in temp_cols:
        df_wx_display[col] = df_wx_display[col].apply(
            lambda x: _c_to_f(x) if pd.notna(x) else x
        )
    df_wx_display = df_wx_display.rename(
        columns={c: c.replace("_c", "_f") for c in temp_cols}
    )

df_fuel_display = df_fuel.copy()
if currency == "USD":
    _rate = _get_isk_rate()
    price_cols = [c for c in df_fuel_display.columns if c.endswith("_isk")]
    for col in price_cols:
        df_fuel_display[col] = df_fuel_display[col] / _rate
    df_fuel_display = df_fuel_display.rename(
        columns={c: c.replace("_isk", "_usd") for c in price_cols}
    )

# ---- Display raw data tables ------------------------------
with st.expander("📋 Weather Telemetry — Live Data (Cached)", expanded=False):
    st.dataframe(df_wx_display, use_container_width=True)
with st.expander("⛽ Fuel Stations — Live Data (Cached)", expanded=False):
    st.dataframe(df_fuel_display, use_container_width=True)

# Show cache info
cached_routes = list_cached_routes()
if cached_routes:
    current_key = _generate_route_key(
        route_info["origin"], route_info["destination"], route_info["waypoints"]
    )
    for cr in cached_routes:
        if cr["route_key"] == current_key:
            st.caption(f"📦 Using cached data: {cr['stations']} stations, {cr['fuel_stops']} fuel stops (created {cr['created'][:19]})")
            break

    st.markdown("---")

    # ==================================================================
    # KPI CARDS
    # ==================================================================
    st.markdown("### 🏆 Key Performance Indicators")

    max_gust = (
        df_wx["wind_gust_max_ms"].dropna().max()
        if not df_wx["wind_gust_max_ms"].dropna().empty
        else 0
    )
    avg_temp = (
        df_wx["air_temp_c"].dropna().mean()
        if not df_wx["air_temp_c"].dropna().empty
        else 0
    )
    open_count = int((df_wx["road_status"] == "Open").sum())
    total_stations = len(df_wx)
    min_fuel = df_fuel["price_isk"].min() if not df_fuel.empty else 0
    cheapest_name = (
        df_fuel.loc[df_fuel["price_isk"].idxmin(), "station_name"]
        if not df_fuel.empty
        else "N/A"
    )

    # Risk classification
    if max_gust >= 18:
        risk_label, risk_desc = "🔴 CRITICAL", "Rollover risk"
    elif max_gust >= 15:
        risk_label, risk_desc = "🟠 HIGH", "Strong crosswinds"
    elif max_gust >= 12:
        risk_label, risk_desc = "🟡 MODERATE", "Caution advised"
    elif max_gust >= 8:
        risk_label, risk_desc = "🟢 LOW", "Moderate winds"
    else:
        risk_label, risk_desc = "✅ CLEAR", "Safe conditions"

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    kpi1.metric("🛡️ Route Risk", risk_label, risk_desc)
    if temp_unit == "°F":
        kpi2.metric("🌡️ Avg Temperature", f"{_c_to_f(avg_temp):.1f}°F")
    else:
        kpi2.metric("🌡️ Avg Temperature", f"{avg_temp:.1f}°C")
    kpi3.metric("🛣️ Roads Open", f"{open_count}/{total_stations}")
    if currency == "USD":
        kpi4.metric("⛽ Cheapest Fuel", f"${_isk_to_usd(min_fuel):.2f}/L", cheapest_name)
    else:
        kpi4.metric("⛽ Cheapest Fuel", f"{min_fuel:.1f} ISK/L", cheapest_name)
    kpi5.metric("💨 Max Wind Gust", f"{max_gust:.1f} m/s")

    st.markdown("---")

    # ==================================================================
    # CHART 1: Road Safety Heatmap
    # ==================================================================
    st.markdown("### 🛣️ Road Safety Heatmap")
    st.caption(
        "Each cell is a normalized 0 → 1 risk score. "
        "Green = safe · Yellow = caution · Red = danger."
    )

    def _norm(series: pd.Series, lo: float, hi: float) -> pd.Series:
        """Normalize a series to [0, 1] given (lo, hi) thresholds."""
        return ((series - lo) / (hi - lo)).clip(0, 1)

    hm = pd.DataFrame({"Station": df_wx["station_name"].values})

    # Wind gust risk  (0 m/s = safe, 18 m/s = critical)
    hm["Wind Gust"] = _norm(df_wx["wind_gust_max_ms"].fillna(0), 0, 18).values

    # Cold risk  (15 °C+ = safe → 0, 0 °C = dangerous → 1)
    hm["Cold Risk"] = (
        1 - _norm(df_wx["air_temp_c"].fillna(5), 0, 15)
    ).values

    # Road surface risk
    if df_wx["road_surface_temp_c"].notna().any():
        hm["Road Surface"] = (
            1 - _norm(df_wx["road_surface_temp_c"].fillna(5), 0, 20)
        ).values

    # Humidity risk  (50 % = fine, 100 % = fog/ice)
    if df_wx["relative_humidity_pct"].notna().any():
        hm["Humidity"] = _norm(
            df_wx["relative_humidity_pct"].fillna(50), 50, 100
        ).values

    # Road status → numeric
    status_map = {
        "Open": 0.0,
        "Caution Advised": 0.5,
        "Gravel / Ice": 0.75,
        "Impassable": 1.0,
    }
    hm["Road Status"] = (
        df_wx["road_status"].map(status_map).fillna(0.25).values
    )

    z = hm.drop(columns=["Station"]).values
    x_labels = list(hm.columns[1:])
    y_labels = list(hm["Station"])

    fig_hm = go.Figure(data=go.Heatmap(
        z=z,
        x=x_labels,
        y=y_labels,
        colorscale=[
            [0.0, "#15803d"],   # deep green – safe
            [0.25, "#22c55e"],  # green
            [0.45, "#fbbf24"],  # gold – caution
            [0.65, "#f97316"],  # orange – warning
            [0.85, "#dc2626"],  # red – danger
            [1.0, "#991b1b"],   # dark red – critical
        ],
        text=[[f"{v:.2f}" for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=13, color="white"),
        hovertemplate=(
            "Station: %{y}<br>"
            "Metric: %{x}<br>"
            "Risk Score: %{z:.2f}<br>"
            "<extra></extra>"
        ),
        colorbar=dict(
            title="Risk",
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["Safe", "Low", "Moderate", "High", "Critical"],
        ),
    ))

    fig_hm.update_layout(
        template="plotly_dark",
        height=max(320, len(y_labels) * 60),
        xaxis_title="Risk Factor",
        xaxis_side="top",
        yaxis_title="",
        yaxis_autorange="reversed",
        margin=dict(l=180, r=20, t=60, b=30),
    )

    st.plotly_chart(fig_hm, use_container_width=True)

    # ==================================================================
    # CHART 2: Wind Speed & Gust Grouped Bar Chart
    # ==================================================================
    st.markdown("### 🌬️ Wind Conditions Along Route")
    st.caption(
        "Grouped bars show average wind speed vs peak gusts at each station. "
        "Dashed lines mark camper-van safety thresholds."
    )

    fig_wind = go.Figure()

    fig_wind.add_trace(go.Bar(
        name="Avg Wind Speed",
        x=df_wx["station_name"],
        y=df_wx["wind_speed_avg_ms"],
        marker_color="#3b82f6",
        text=df_wx["wind_speed_avg_ms"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else ""
        ),
        textposition="outside",
        textfont=dict(size=11),
    ))

    fig_wind.add_trace(go.Bar(
        name="Max Wind Gust",
        x=df_wx["station_name"],
        y=df_wx["wind_gust_max_ms"],
        marker_color="#ef4444",
        text=df_wx["wind_gust_max_ms"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else ""
        ),
        textposition="outside",
        textfont=dict(size=11),
    ))

    # Danger thresholds
    fig_wind.add_hline(
        y=15, line_dash="dash", line_color="#f59e0b", line_width=2,
        annotation_text="⚠️ Strong crosswind (15 m/s)",
        annotation_position="top left",
        annotation_font=dict(color="#f59e0b", size=12),
    )
    fig_wind.add_hline(
        y=18, line_dash="dash", line_color="#dc2626", line_width=2,
        annotation_text="🚨 Rollover risk (18 m/s)",
        annotation_position="top left",
        annotation_font=dict(color="#dc2626", size=12),
    )

    fig_wind.update_layout(
        barmode="group",
        template="plotly_dark",
        height=450,
        yaxis_title="Wind Speed (m/s)",
        xaxis_title="Weather Station",
        xaxis_tickangle=-30,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ),
        margin=dict(l=50, r=20, t=50, b=100),
    )

    st.plotly_chart(fig_wind, use_container_width=True)

    # ==================================================================
    # CHART 3: Fuel Price Comparison (Horizontal Bar)
    # ==================================================================
    st.markdown("### ⛽ Fuel Price Comparison by Station")
    st.caption(
        "Bars sorted cheapest → most expensive. ⭐ markers show discount-card "
        "prices where available."
    )

    brand_colors = {
        "Orkan": "#22c55e",
        "Atlantsolía": "#f97316",
        "N1": "#ef4444",
        "Olís": "#3b82f6",
        "Skeljungur": "#8b5cf6",
        "Costco": "#06b6d4",
    }

    # --- Unit-aware column & label helpers ---
    price_col = "price_usd" if currency == "USD" else "price_isk"
    discount_col = "discount_price_usd" if currency == "USD" else "discount_price_isk"
    price_unit = "USD/L" if currency == "USD" else "ISK/L"
    price_prefix = "$" if currency == "USD" else ""

    df_fuel_top = df_fuel_display.sort_values(price_col, ascending=True).head(15)
    bar_colors = [brand_colors.get(c, "#6b7280") for c in df_fuel_top["company"]]

    fig_fuel = go.Figure()

    fig_fuel.add_trace(go.Bar(
        y=df_fuel_top["station_name"],
        x=df_fuel_top[price_col],
        orientation="h",
        marker_color=bar_colors,
        text=df_fuel_top.apply(
            lambda r: f"{price_prefix}{r[price_col]:.1f} {price_unit}  •  {r['company']}", axis=1
        ),
        textposition="outside",
        textfont=dict(size=11),
        name="Regular Price",
        hovertemplate=(
            "<b>%{y}</b><br>"
            f"Price: %{{x:.1f}} {price_unit}<br>"
            "<extra></extra>"
        ),
    ))

    # Discount price star markers
    has_discount = df_fuel_top[discount_col].notna()
    if has_discount.any():
        fig_fuel.add_trace(go.Scatter(
            y=df_fuel_top.loc[has_discount, "station_name"],
            x=df_fuel_top.loc[has_discount, discount_col],
            mode="markers+text",
            marker=dict(
                size=14, color="#fbbf24", symbol="star",
                line=dict(width=1, color="#000"),
            ),
            text=df_fuel_top.loc[has_discount, discount_col].apply(
                lambda v: f"{v:.1f}"
            ),
            textposition="middle left",
            textfont=dict(size=10, color="#fbbf24"),
            name="Discount Price",
            hovertemplate=f"Discount: %{{x:.1f}} {price_unit}<extra></extra>",
        ))

    fig_fuel.update_layout(
        template="plotly_dark",
        height=max(420, len(df_fuel_top) * 36),
        xaxis_title=f"Price ({price_unit})",
        yaxis_title="",
        showlegend=bool(has_discount.any()),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ),
        margin=dict(l=180, r=120, t=30, b=50),
    )

    st.plotly_chart(fig_fuel, use_container_width=True)
