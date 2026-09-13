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
    "Paste any Google Maps directions URL you build on Google Maps."
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
        
        # Plotly 6.x: use px.line_map (line_mapbox was removed)
        pts = pts.rename(columns={"lng": "lon"})
        fig = px.line_map(
            pts, lat="lat", lon="lon", zoom=6, height=480,
            title="Route Overview",
        )
        # Hide legend for the route line
        fig.data[0].update(showlegend=False, name="Route", hoverinfo="skip")
        
        # Add origin marker (green) - use go.Scattermap directly to avoid duplicate legend entries
        origin_m = markers[markers["type"] == "origin"]
        if not origin_m.empty:
            fig.add_trace(go.Scattermap(
                lat=origin_m["lat"],
                lon=origin_m["lon"],
                mode="markers",
                marker=dict(size=16, color="#22c55e", symbol="circle"),
                name="Origin",
                showlegend=True,
                hovertext=origin_m["name"],
                hovertemplate="<b>%{hovertext}</b><br>Origin<extra></extra>",
            ))
        
        # Add waypoint markers (blue) - use go.Scattermap directly
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
                fig.add_trace(go.Scattermap(
                    lat=wp_df["lat"],
                    lon=wp_df["lon"],
                    mode="markers",
                    marker=dict(size=14, color="#3b82f6", symbol="circle"),
                    name="Waypoint",
                    showlegend=True,
                    hovertext=wp_df["name"],
                    hovertemplate="<b>%{hovertext}</b><br>Waypoint<extra></extra>",
                ))
        
        # Add destination marker (red) - use go.Scattermap directly
        dest_m = markers[markers["type"] == "destination"]
        if not dest_m.empty:
            fig.add_trace(go.Scattermap(
                lat=dest_m["lat"],
                lon=dest_m["lon"],
                mode="markers",
                marker=dict(size=16, color="#ef4444", symbol="circle"),
                name="Destination",
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
        fig.add_trace(px.scatter_map(
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
            map_style="carto-positron",  # Lighter style with English labels
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
            fig.update_layout(map_mapbox_accesstoken=mapbox_token)
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

# Sort fuel stations by route order (distance from origin), not distance from route
# distance_from_origin_km = cumulative distance along route from origin
# distance_km = perpendicular distance from route (how far off route)
if "distance_from_origin_km" in df_fuel.columns:
    df_fuel = df_fuel.sort_values("distance_from_origin_km", ascending=True).reset_index(drop=True)
else:
    # Fallback: use distance from route if route distance not available
    df_fuel = df_fuel.sort_values("distance_km", ascending=True).reset_index(drop=True)

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

# ---- Round fuel prices to 2 decimal places (ISK/USD) ----
_price_cols = [c for c in df_fuel_display.columns
               if c in ("price_isk", "regular_price_isk", "discount_price_isk",
                        "price_usd", "regular_price_usd", "discount_price_usd")]
for _col in _price_cols:
    df_fuel_display[_col] = df_fuel_display[_col].apply(
        lambda v: round(v, 2) if pd.notna(v) else v
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

    # Risk classification tuned for Renault Trafic 3 / medium panel van camper (~2.5m height, ~2.5-3t)
    # Based on Icelandic rental company guidance & Vegagerðin high-sided vehicle advice
    if max_gust >= 25:
        risk_label, risk_desc = "🔴 CRITICAL", "DO NOT DRIVE - Park safely immediately"
    elif max_gust >= 22:
        risk_label, risk_desc = "🔴 SEVERE", "High risk - Seek shelter, avoid exposed areas"
    elif max_gust >= 18:
        risk_label, risk_desc = "🟠 HIGH", "High caution - Consider stopping, crosswind danger"
    elif max_gust >= 12:
        risk_label, risk_desc = "🟡 MODERATE", "Caution - Reduce speed, firm grip on wheel"
    elif max_gust >= 8:
        risk_label, risk_desc = "🟢 LOW", "Normal driving - Be aware of gusts"
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
        kpi4.metric("⛽ Cheapest Fuel", f"{min_fuel:.2f} ISK/L", cheapest_name)
    kpi5.metric("💨 Max Wind Gust", f"{max_gust:.1f} m/s")

    st.markdown("---")

    # ==================================================================
    # NEW: Weather Alerts Timeline - Route-based alert visualization
    # ==================================================================
    st.markdown("### ⚠️ Weather Alerts Along Route")
    
    # Map Vedur's Icelandic weather-type labels to English translations.
    # (Forecast/observation text is returned by the API in Icelandic.)
    WEATHER_TRANSLATIONS = {
        "Alskýjað": "Overcast", "Skýjað": "Cloudy", "Léttskýjað": "Partly Cloudy",
        "Heiðskírt": "Fair / Clear", "Lítils háttar rigning": "Light Rain",
        "Rigning": "Rain", "Mikil rigning": "Heavy Rain", "Skúrir": "Showers",
        "Snjór": "Snow", "Snjóskúrir": "Snow Showers", "Frost": "Frost",
        "Gluggaveður": "Gluggaveður (Window Weather)", "Dimma": "Fog",
        "Rok": "Blowing Snow", "Hlý": "Mild", "Kaldi": "Cold",
    }
    
    # Check if we have forecast weather data
    if "forecast_weather_type" in df_wx.columns and df_wx["forecast_weather_type"].notna().any():
        # Prepare alert data
        alert_data = []
        for _, row in df_wx.iterrows():
            if pd.notna(row.get("forecast_weather_type")):
                icelandic = row["forecast_weather_type"]
                english = WEATHER_TRANSLATIONS.get(icelandic, icelandic)
                station = row["station_name"]
                wind = row.get("forecast_wind_speed_ms", 0)
                
                # Determine severity
                # Match severe conditions exactly (a substring match would also
                # flag "Light Rain" because it contains "Rigning"/"Rok" as a suffix).
                severe_weather = {"Mikil rigning", "Rigning", "Snjór", "Snjóskúrir", "Frost", "Dimma", "Rok"}
                has_severe = icelandic in severe_weather
                
                if wind >= 22 or has_severe:
                    alert_level = "danger"
                    alert_color = "#dc2626"  # Red
                elif wind >= 18:
                    alert_level = "warning"
                    alert_color = "#f97316"  # Orange
                elif wind >= 12:
                    alert_level = "caution"
                    alert_color = "#f59e0b"  # Yellow
                else:
                    alert_level = "safe"
                    alert_color = "#10b981"  # Green
                
                alert_data.append({
                    "station": station,
                    "english": english,
                    "level": alert_level,
                    "color": alert_color,
                    "wind": wind
                })
        if alert_data:
            # Create timeline
            fig_alerts = go.Figure()
            
            # Route line
            fig_alerts.add_trace(go.Scatter(
                x=list(range(len(alert_data))),
                y=[0] * len(alert_data),
                mode="lines",
                line=dict(color="#6b7280", width=4),
                showlegend=False,
                hoverinfo="skip",
            ))
            
            # Alert markers
            alert_x = list(range(len(alert_data)))
            alert_y = [0] * len(alert_data)
            alert_text = []
            marker_colors = []
            marker_sizes = []
            marker_symbols = []
            
            for alert in alert_data:
                hover = f"<b>{alert['station']}</b><br>Weather: {alert['english']}<br>"
                hover += f"Wind: {alert['wind']:.1f} m/s<br><i>{alert['level'].upper()}</i>"
                alert_text.append(hover)
                marker_colors.append(alert['color'])
                
                if alert['level'] == "danger":
                    marker_sizes.append(20)
                    marker_symbols.append("triangle-down")
                elif alert['level'] == "warning":
                    marker_sizes.append(18)
                    marker_symbols.append("triangle-down")
                elif alert['level'] == "caution":
                    marker_sizes.append(16)
                    marker_symbols.append("triangle-down")
                else:
                    marker_sizes.append(14)
                    marker_symbols.append("circle")
            
            fig_alerts.add_trace(go.Scatter(
                x=alert_x,
                y=alert_y,
                mode="markers+text",
                marker=dict(
                    size=marker_sizes,
                    color=marker_colors,
                    symbol=marker_symbols,
                    line=dict(width=2, color="white")
                ),
                text=[a["english"] for a in alert_data],
                textposition="top center",
                textfont=dict(size=10, color="white", family="Arial Black"),
                hovertext=alert_text,
                hoverinfo="text",
                showlegend=False
            ))
            
            # START and END markers
            fig_alerts.add_trace(go.Scatter(
                x=[-0.5], y=[0], mode="markers+text",
                marker=dict(size=18, color="#10b981", symbol="triangle-up", line=dict(width=2, color="white")),
                text=["START"], textposition="bottom center",
                textfont=dict(size=10, color="#10b981", family="Arial Black"),
                showlegend=False, hoverinfo="skip"
            ))
            
            fig_alerts.add_trace(go.Scatter(
                x=[len(alert_data) - 0.5], y=[0], mode="markers+text",
                marker=dict(size=18, color="#dc2626", symbol="triangle-down", line=dict(width=2, color="white")),
                text=["END"], textposition="bottom center",
                textfont=dict(size=10, color="#dc2626", family="Arial Black"),
                showlegend=False, hoverinfo="skip"
            ))
            
            fig_alerts.update_layout(
                template="plotly_dark", height=200,
                xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
                yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, range=[-1, 1]),
                margin=dict(l=20, r=20, t=10, b=10),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                hovermode="closest"
            )
            
            st.caption(
                "Markers show weather alerts along your route. "
                "Colors: 🔴 Red (danger) | 🟠 Orange (warning) | 🟡 Yellow (caution) | 🟢 Green (safe). "
                "Hover for Name of Waypoints and Details."
            )
            
            st.plotly_chart(fig_alerts, use_container_width=True)
        else:
            st.info("No weather alert data available.")
    else:
        st.info("Forecast weather data not available for alerts.")

    # ==================================================================
    # CHART 1: Road Safety Heatmap (Forecast at ETA)
    # ==================================================================
    st.markdown("### 🛣️ Road Safety Heatmap")
    st.caption(
        "🔮 **Based on FORECAST at your ETA** (when you arrive at each station), not current conditions. "
        "Green = safe · Yellow = caution · Red = danger. "
        "Wind: Vedur.is warnings | Temp: Black ice risk | Surface: Ice formation | Humidity: Fog/ice combo"
    )

    # Check if forecast data is available
    has_forecast = (
        "forecast_wind_speed_ms" in df_wx.columns and 
        df_wx["forecast_wind_speed_ms"].notna().any()
    )

    # Use forecast at ETA if available, otherwise fall back to current observations
    if has_forecast:
        # The Vedur forecast API exposes wind SPEED (F), not wind gust, so we use the
        # forecast wind speed here; current observations remain wind_gust_max_ms.
        wind_source = df_wx["forecast_wind_speed_ms"]
        temp_source = df_wx["forecast_temp_c"]
        source_label = " (Forecast at ETA)"
    else:
        wind_source = df_wx["wind_gust_max_ms"]
        temp_source = df_wx["air_temp_c"]
        source_label = " (Current - No Forecast)"

    def _norm(series: pd.Series, lo: float, hi: float) -> pd.Series:
        """Normalize a series to [0, 1] given (lo, hi) thresholds."""
        return ((series - lo) / (hi - lo)).clip(0, 1)

    hm = pd.DataFrame({"Station": df_wx["station_name"].values})

    # Wind gust risk for Renault Trafic 3 / medium panel van camper
    # 0-8 m/s = safe (0), 8-12 m/s = low (0→0.2), 12-18 m/s = moderate (0.2→0.5)
    # 18-22 m/s = high (0.5→0.75), 22-25 m/s = severe (0.75→0.9), 25+ m/s = critical (0.9→1.0)
    def _wind_gust_risk(gust_ms):
        """Piecewise normalization for medium panel van camper (Renault Trafic 3 class)."""
        g = gust_ms.fillna(0)
        return (
            (g.clip(upper=8) * 0.0)                    # 0-8: 0 risk
            + ((g.clip(8, 12) - 8) / 4 * 0.2)         # 8-12: 0→0.2
            + ((g.clip(12, 18) - 12) / 6 * 0.3)       # 12-18: 0.2→0.5
            + ((g.clip(18, 22) - 18) / 4 * 0.25)      # 18-22: 0.5→0.75
            + ((g.clip(22, 25) - 22) / 3 * 0.15)      # 22-25: 0.75→0.9
            + ((g - 25).clip(lower=0) / 10 * 0.1).clip(upper=0.1)  # 25+: 0.9→1.0
        ).clip(0, 1)

    hm["Wind Gust"] = _wind_gust_risk(wind_source).values

    # Cold / Black ice risk: real danger zone is 5°C → 0°C and below
    # Above 5°C = safe (0), 5→0°C = rising risk (0→0.7), below 0°C = severe (0.7→1.0)
    def _cold_risk(temp_c):
        t = temp_c.fillna(5)
        # Above 5°C: no risk
        # 5°C to 0°C: 0 to 0.7 (black ice risk increases)
        # Below 0°C: 0.7 to 1.0 (ice certain)
        risk = pd.Series(0.0, index=t.index)
        risk = risk.where(t > 5, 0.0)  # Above 5°C = 0
        # 5°C down to 0°C
        mask_5_0 = (t <= 5) & (t >= 0)
        risk = risk.where(~mask_5_0, (5 - t[mask_5_0]) / 5 * 0.7)
        # Below 0°C
        mask_below_0 = t < 0
        risk = risk.where(~mask_below_0, 0.7 + (-t[mask_below_0] / 15 * 0.3).clip(upper=0.3))
        return risk.clip(0, 1)

    hm["Cold Risk"] = _cold_risk(temp_source).values

    # Road surface temperature risk: ice forms at/below 0°C, risk rises 3°C→0°C
    # Use forecast temp as proxy (no direct road surface forecast available)
    if df_wx["road_surface_temp_c"].notna().any():
        def _road_surface_risk(surf_temp_c):
            t = surf_temp_c.fillna(5)
            risk = pd.Series(0.0, index=t.index)
            risk = risk.where(t > 3, 0.0)  # Above 3°C = safe
            mask_3_0 = (t <= 3) & (t >= 0)
            risk = risk.where(~mask_3_0, (3 - t[mask_3_0]) / 3 * 0.7)  # 3→0°C = 0→0.7
            mask_below_0 = t < 0
            risk = risk.where(~mask_below_0, 0.7 + (-t[mask_below_0] / 10 * 0.3).clip(upper=0.3))
            return risk.clip(0, 1)

        # Use current road surface temp (no forecast available) but flag it
        hm["Road Surface"] = _road_surface_risk(df_wx["road_surface_temp_c"]).values
        hm["Road Surface Source"] = "Current"
    else:
        hm["Road Surface"] = 0.0
        hm["Road Surface Source"] = "N/A"

    # Humidity risk: only dangerous at high humidity (>90%) combined with low temps
    # Below 80% = low risk, 80-95% = rising, 95%+ = high (fog/ice formation)
    # Use current humidity (no forecast available)
    if df_wx["relative_humidity_pct"].notna().any():
        def _humidity_risk(humidity_pct):
            h = humidity_pct.fillna(50)
            risk = pd.Series(0.0, index=h.index)
            risk = risk.where(h < 80, 0.0)  # Below 80% = minimal risk
            mask_80_95 = (h >= 80) & (h <= 95)
            risk = risk.where(~mask_80_95, (h[mask_80_95] - 80) / 15 * 0.6)  # 80-95% = 0→0.6
            mask_above_95 = h > 95
            risk = risk.where(~mask_above_95, 0.6 + (h[mask_above_95] - 95) / 5 * 0.4)  # 95-100% = 0.6→1.0
            return risk.clip(0, 1)

        hm["Humidity"] = _humidity_risk(df_wx["relative_humidity_pct"]).values
        hm["Humidity Source"] = "Current"
    else:
        hm["Humidity"] = 0.0
        hm["Humidity Source"] = "N/A"

    status_map = {
        "Open": 0.0,
        "Caution Advised": 0.5,
        "Gravel / Ice": 0.75,
        "Impassable": 1.0,
    }
    hm["Road Status"] = (
        df_wx["road_status"].map(status_map).fillna(0.25).values
    )

    # Only numeric risk columns for heatmap (exclude source indicator columns)
    risk_cols = [c for c in hm.columns if c != "Station" and hm[c].dtype in ["float64", "int64", "float32", "int32"]]
    z = hm[risk_cols].values
    x_labels = risk_cols
    y_labels = list(hm["Station"])

    fig_hm = go.Figure(data=go.Heatmap(
        z=z,
        x=x_labels,
        y=y_labels,
        zmin=0,
        zmax=1,
        colorscale=[
            [0.0,  "#22c55e"],  # green – safe
            [0.25, "#86efac"],  # light green – low
            [0.5,  "#fbbf24"],  # gold – moderate
            [0.75, "#f97316"],  # orange – high
            [1.0,  "#dc2626"],  # red – critical
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
    # NEW: Temperature Timeline Chart (Current + Forecast at ETA)
    # ==================================================================
    st.markdown("### 🌡️ Temperature Timeline Along Route")
    
    has_forecast_temp = "forecast_temp_c" in df_wx.columns and df_wx["forecast_temp_c"].notna().any()
    
    fig_temp = go.Figure()
    
    # Current air temperature (solid line)
    fig_temp.add_trace(go.Scatter(
        x=df_wx["station_name"],
        y=df_wx["air_temp_c"],
        mode="lines+markers",
        name="▮ Current Air Temp",
        line=dict(color="#3b82f6", width=3),
        marker=dict(size=8, color="#3b82f6"),
        hovertemplate="<b>%{x}</b><br>Current: %{y:.1f}°C<extra></extra>",
    ))
    
    # Current road surface temperature (dashed line)
    if df_wx["road_surface_temp_c"].notna().any():
        fig_temp.add_trace(go.Scatter(
            x=df_wx["station_name"],
            y=df_wx["road_surface_temp_c"],
            mode="lines+markers",
            name="▮ Current Road Surface",
            line=dict(color="#f97316", width=2, dash="dot"),
            marker=dict(size=6, color="#f97316"),
            hovertemplate="<b>%{x}</b><br>Road Surface: %{y:.1f}°C<extra></extra>",
        ))
    
    # Forecast temperature at ETA (dashed line with different color)
    if has_forecast_temp:
        fig_temp.add_trace(go.Scatter(
            x=df_wx["station_name"],
            y=df_wx["forecast_temp_c"],
            mode="lines+markers",
            name="▫️ Forecast Air Temp (at ETA)",
            line=dict(color="#60a5fa", width=3, dash="dash"),
            marker=dict(size=8, color="#60a5fa", symbol="diamond"),
            hovertemplate="<b>%{x}</b><br>Forecast at ETA: %{y:.1f}°C<extra></extra>",
        ))
    
    # Black ice danger zone (0°C to 5°C)
    fig_temp.add_hrect(
        y0=0, y1=5,
        fillcolor="rgba(220, 38, 38, 0.15)",
        line_width=0,
        annotation_text="⚠️ Black Ice Risk Zone (0–5°C)",
        annotation_position="top left",
        annotation_font=dict(color="#dc2626", size=10),
    )
    
    # Freezing line
    fig_temp.add_hline(
        y=0, line_dash="dash", line_color="#dc2626", line_width=2,
        annotation_text="🧊 Freezing (0°C)",
        annotation_position="bottom right",
        annotation_font=dict(color="#dc2626", size=10),
    )
    
    # Safe zone line
    fig_temp.add_hline(
        y=5, line_dash="dash", line_color="#22c55e", line_width=1,
        annotation_text="✅ Safe (>5°C)",
        annotation_position="top right",
        annotation_font=dict(color="#22c55e", size=10),
    )
    
    fig_temp.update_layout(
        template="plotly_dark",
        height=400,
        yaxis_title="Temperature (°C)",
        xaxis_title="Weather Station (in route order)",
        xaxis_tickangle=-30,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ),
        margin=dict(l=50, r=20, t=50, b=100),
        hovermode="x unified",
    )
    
    st.caption(
        "▮ **Solid lines** = Current observations | "
        "▫️ **Dashed line** = Forecast at your ETA | "
        "Red zone = Black ice risk (road surface near freezing)"
    )
    
    st.plotly_chart(fig_temp, use_container_width=True)

    # ==================================================================
    # CHART 2: Wind Speed & Gust Grouped Bar Chart (Current Observations Only)
    # ==================================================================
    st.markdown("### 🌬️ Wind Conditions Along Route")
    st.caption(
        "▮ **Solid bars** = Current observations (now). "
        "▫️ **Dashed line** = Forecast wind at your ETA. "
        "Dashed lines mark camper-van safety thresholds."
    )

    fig_wind = go.Figure()

    # --- Current Observations (solid bars) ---
    fig_wind.add_trace(go.Bar(
        name="▮ Current Avg Wind",
        x=df_wx["station_name"],
        y=df_wx["wind_speed_avg_ms"],
        marker_color="#3b82f6",
        text=df_wx["wind_speed_avg_ms"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else ""
        ),
        textposition="outside",
        textfont=dict(size=11),
        legendgroup="current",
        offsetgroup=0,
    ))

    fig_wind.add_trace(go.Bar(
        name="▮ Current Max Gust",
        x=df_wx["station_name"],
        y=df_wx["wind_gust_max_ms"],
        marker_color="#ef4444",
        text=df_wx["wind_gust_max_ms"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else ""
        ),
        textposition="outside",
        textfont=dict(size=11),
        legendgroup="current",
        offsetgroup=1,
    ))

    # --- Forecast Wind Speed at ETA (dashed line) ---
    if "forecast_wind_speed_ms" in df_wx.columns and df_wx["forecast_wind_speed_ms"].notna().any():
        fig_wind.add_trace(go.Scatter(
            name="▫️ Forecast Wind (at ETA)",
            x=df_wx["station_name"],
            y=df_wx["forecast_wind_speed_ms"],
            mode="lines+markers",
            line=dict(color="#60a5fa", width=3, dash="dash"),
            marker=dict(size=8, color="#60a5fa", symbol="diamond"),
            hovertemplate="<b>%{x}</b><br>Forecast at ETA: %{y:.1f} m/s<extra></extra>",
        ))

    # Danger thresholds (for Trafic 3 camper van)
    fig_wind.add_hline(
        y=12, line_dash="dash", line_color="#f59e0b", line_width=2,
        annotation_text="⚠️ Caution for campers (12 m/s)",
        annotation_position="top left",
        annotation_font=dict(color="#f59e0b", size=11),
    )
    fig_wind.add_hline(
        y=18, line_dash="dash", line_color="#f97316", line_width=2,  # Orange for high risk
        annotation_text="🟠 High risk - consider stopping (18 m/s)",
        annotation_position="top left",
        annotation_font=dict(color="#f97316", size=11),
    )
    fig_wind.add_hline(
        y=22, line_dash="dash", line_color="#dc2626", line_width=3,  # Dark red for severe
        annotation_text="🔴 SEVERE - seek shelter (22 m/s)",
        annotation_position="top left",
        annotation_font=dict(color="#dc2626", size=12, family="Arial Black"),
    )

    fig_wind.update_layout(
        barmode="group",
        template="plotly_dark",
        height=450,
        yaxis_title="Wind Speed (m/s)",
        xaxis_title="Weather Station (in route order)",
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
            lambda r: f"{price_prefix}{r[price_col]:.2f} {price_unit}  •  {r['company']}", axis=1
        ),
        textposition="outside",
        textfont=dict(size=11),
        name="Regular Price",
        hovertemplate=(
            "<b>%{y}</b><br>"
            f"Price: %{{x:.2f}} {price_unit}<br>"
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
                lambda v: f"{v:.2f}"
            ),
            textposition="middle left",
            textfont=dict(size=10, color="#fbbf24"),
            name="Discount Price",
            hovertemplate=f"Discount: %{{x:.2f}} {price_unit}<extra></extra>",
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
