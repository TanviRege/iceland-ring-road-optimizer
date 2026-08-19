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
        mapbox_token = os.environ.get("MAPBOX_TOKEN", "")
        if mapbox_token:
            fig.update_layout(mapbox_accesstoken=mapbox_token)
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:  # pragma: no cover
    st.caption(f"Map preview unavailable: {exc}")

# --------------------------------------------------------------------------- #
# 5. Route Analytics Dashboard (DuckDB)
# --------------------------------------------------------------------------- #
st.markdown("---")
st.markdown("## 📊 Route Analytics Dashboard")
st.caption(
    "Weather conditions and fuel economics from the DuckDB analytics pipeline. "
    "Data sourced from Icelandic Met Office (Veður) and Gasvaktin fuel price feeds."
)

from pathlib import Path
import plotly.graph_objects as go

DATA_DIR = Path(__file__).parent / "data"
_telemetry_path = DATA_DIR / "iceland_raw_telemetry.parquet"
_fuel_path = DATA_DIR / "iceland_fuel_stations.parquet"

if not _telemetry_path.exists() or not _fuel_path.exists():
    st.warning(
        "⚠️ Parquet data files not found in `data/`. "
        "Run the data ingestion pipeline first."
    )
else:
    import duckdb

    # ---- Load data via DuckDB ------------------------------------------
    df_wx = duckdb.sql(f"""
        SELECT
            waypoint_id,
            station_name,
            stop_name,
            air_temp_c,
            air_temp_max_c,
            air_temp_min_c,
            wind_speed_avg_ms,
            wind_speed_max_ms,
            wind_gust_max_ms,
            wind_dir_deg,
            wind_dir_cardinal,
            relative_humidity_pct,
            sea_level_pressure_hpa,
            road_surface_temp_c,
            road_surface_temp_max_c,
            road_surface_temp_min_c,
            road_status,
            observation_time_utc
        FROM read_parquet('{_telemetry_path}')
        ORDER BY waypoint_id
    """).df()

    df_fuel = duckdb.sql(f"""
        SELECT
            station_name,
            company,
            price_isk,
            regular_price_isk,
            discount_price_isk,
            distance_km
        FROM read_parquet('{_fuel_path}')
        ORDER BY price_isk
    """).df()

    # ---- Display raw DuckDB result tables ------------------------------
    with st.expander("📋 Weather Telemetry — DuckDB Query Results", expanded=False):
        st.dataframe(df_wx, use_container_width=True)
    with st.expander("⛽ Fuel Stations — DuckDB Query Results", expanded=False):
        st.dataframe(df_fuel, use_container_width=True)

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
    kpi2.metric("🌡️ Avg Temperature", f"{avg_temp:.1f}°C")
    kpi3.metric("🛣️ Roads Open", f"{open_count}/{total_stations}")
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

    df_fuel_top = df_fuel.sort_values("price_isk", ascending=True).head(15)
    bar_colors = [brand_colors.get(c, "#6b7280") for c in df_fuel_top["company"]]

    fig_fuel = go.Figure()

    fig_fuel.add_trace(go.Bar(
        y=df_fuel_top["station_name"],
        x=df_fuel_top["price_isk"],
        orientation="h",
        marker_color=bar_colors,
        text=df_fuel_top.apply(
            lambda r: f"{r['price_isk']:.1f} ISK/L  •  {r['company']}", axis=1
        ),
        textposition="outside",
        textfont=dict(size=11),
        name="Regular Price",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Price: %{x:.1f} ISK/L<br>"
            "<extra></extra>"
        ),
    ))

    # Discount price star markers
    has_discount = df_fuel_top["discount_price_isk"].notna()
    if has_discount.any():
        fig_fuel.add_trace(go.Scatter(
            y=df_fuel_top.loc[has_discount, "station_name"],
            x=df_fuel_top.loc[has_discount, "discount_price_isk"],
            mode="markers+text",
            marker=dict(
                size=14, color="#fbbf24", symbol="star",
                line=dict(width=1, color="#000"),
            ),
            text=df_fuel_top.loc[has_discount, "discount_price_isk"].apply(
                lambda v: f"{v:.1f}"
            ),
            textposition="middle left",
            textfont=dict(size=10, color="#fbbf24"),
            name="Discount Price",
            hovertemplate="Discount: %{x:.1f} ISK/L<extra></extra>",
        ))

    fig_fuel.update_layout(
        template="plotly_dark",
        height=max(420, len(df_fuel_top) * 36),
        xaxis_title="Price (ISK/L)",
        yaxis_title="",
        showlegend=bool(has_discount.any()),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ),
        margin=dict(l=180, r=120, t=30, b=50),
    )

    st.plotly_chart(fig_fuel, use_container_width=True)
