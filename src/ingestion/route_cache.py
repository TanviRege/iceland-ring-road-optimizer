
"""
Route-based Data Caching for Iceland Ring Road Optimizer

Hybrid caching strategy:
- CACHE (persistent): Route geometry, station matching, waypoint order (immutable)
- LIVE with TTL: Weather data (1hr), Fuel prices (6hr) - fetched fresh each session
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.ingestion.vedur_station_mapper import VedurRouteWeatherMapper
from src.data_sources.fuel_price_fetcher import get_fuel_price_at_route
from src.interface.directions import get_directions


CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "route_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WEATHER_TTL = 3600      # 1 hour
FUEL_TTL = 21600        # 6 hours
STRUCTURE_TTL = 86400 * 30  # 30 days for route structure


def _generate_route_key(origin: str, destination: str, waypoints: Optional[List[str]] = None) -> str:
    route_data = {"origin": origin.strip().lower(), "destination": destination.strip().lower(), "waypoints": [wp.strip().lower() for wp in (waypoints or [])]}
    return hashlib.sha256(json.dumps(route_data, sort_keys=True).encode()).hexdigest()[:16]


def _get_cache_paths(route_key: str) -> Tuple[Path, Path, Path, Path]:
    route_dir = CACHE_DIR / route_key
    route_dir.mkdir(parents=True, exist_ok=True)
    return (
        route_dir / "route_structure.json",
        route_dir / "weather_live.parquet",
        route_dir / "fuel_live.parquet",
        route_dir / "metadata.json"
    )


def _directions_to_waypoints(directions: Dict) -> List[Tuple[float, float]]:
    """Extract waypoints as (lat, lng) tuples from Google Directions response."""
    waypoints = []
    for leg in directions.get("legs", []):
        for step in leg.get("steps", []):
            loc = step.get("start_location")
            if loc:
                waypoints.append((loc["lat"], loc["lng"]))
    if directions.get("legs"):
        last_leg = directions["legs"][-1]
        end_loc = last_leg.get("end_location")
        if end_loc:
            waypoints.append((end_loc["lat"], end_loc["lng"]))
    return waypoints


def _directions_to_waypoints_with_distance(directions: Dict) -> List[Tuple[float, float, float]]:
    """
    Extract waypoints with cumulative route distance from origin.
    Returns list of (lat, lng, distance_from_origin_km) tuples.
    """
    waypoints = []
    cumulative_distance_km = 0.0
    
    for leg in directions.get("legs", []):
        for step in leg.get("steps", []):
            loc = step.get("start_location")
            if loc:
                waypoints.append((loc["lat"], loc["lng"], cumulative_distance_km))
            # Add step distance to cumulative
            step_dist = step.get("distance", {}).get("value", 0)  # in meters
            cumulative_distance_km += step_dist / 1000.0
    
    if directions.get("legs"):
        last_leg = directions["legs"][-1]
        end_loc = last_leg.get("end_location")
        if end_loc:
            waypoints.append((end_loc["lat"], end_loc["lng"], cumulative_distance_km))
    
    return waypoints


def _is_cache_valid(metadata_path, ttl_seconds: int) -> bool:
    if not metadata_path.exists():
        return False
    try:
        with open(metadata_path) as f:
            meta = json.load(f)
        age = time.time() - meta.get("timestamp", 0)
        return age < ttl_seconds
    except Exception:
        return False


def _save_metadata(metadata_path, data_type: str):
    meta = {"timestamp": time.time(), "type": data_type}
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                meta = json.load(f)
        except Exception:
            pass
    meta[f"{data_type}_updated"] = time.time()
    with open(metadata_path, "w") as f:
        json.dump(meta, f)


def _load_route_structure(structure_path):
    if not structure_path.exists():
        return None
    try:
        with open(structure_path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_route_structure(structure_path, directions, matched_stations, route_order):
    structure = {
        "directions": directions,
        "matched_stations": matched_stations,
        "route_order": route_order,
        "created": time.time()
    }
    with open(structure_path, "w") as f:
        json.dump(structure, f)

def _create_telemetry_from_live_data(
    directions: Dict,
    matched_stations: List[Dict],
    weather_df: pd.DataFrame,
    fuel_stations: List[Dict],
    route_order: Dict,
    forecast_df: pd.DataFrame = None
) -> pd.DataFrame:
    records = []
    from src.ingestion.vedur_station_mapper import haversine_km
    
    # Build forecast lookup by station_name
    forecast_by_station = {}
    if forecast_df is not None and not forecast_df.empty:
        for _, row in forecast_df.iterrows():
            forecast_by_station[row["station_name"].strip().lower()] = row
    
    route_waypoints = _directions_to_waypoints(directions)
    for i, station in enumerate(matched_stations):
        station_name = station.get("station_name", "")
        station_name_lower = station_name.strip().lower()
        weather_row = None
        if not weather_df.empty and "station_name" in weather_df.columns:
            matches = weather_df[weather_df["station_name"].str.strip().str.lower() == station_name_lower]
            if not matches.empty:
                weather_row = matches.iloc[0]
        
        # Get forecast row for this station
        forecast_row = forecast_by_station.get(station_name_lower)
        
        # Find nearest fuel station by geographic proximity
        fuel_info = {}
        lat = station.get("station_lat") or station.get("lat")
        lon = station.get("station_lon") or station.get("lon")
        if lat and lon and fuel_stations:
            min_dist = float("inf")
            for fs in fuel_stations:
                fs_lat = fs.get("lat") or fs.get("geo", {}).get("lat")
                fs_lon = fs.get("lon") or fs.get("geo", {}).get("lon")
                if fs_lat and fs_lon:
                    d = haversine_km(lat, lon, fs_lat, fs_lon)
                    if d < min_dist:
                        min_dist = d
                        fuel_info = fs
            # Only use if within reasonable distance (30km)
            if min_dist > 30.0:
                fuel_info = {}
        
        # Calculate distance from station to nearest point on route
        distance_km = 0.0
        if lat and lon and route_waypoints:
            min_dist = float("inf")
            for rp in route_waypoints:
                d = haversine_km(lat, lon, rp[0], rp[1])
                if d < min_dist:
                    min_dist = d
            distance_km = round(min_dist, 2)
        route_pos = route_order.get(station_name, i)
        record = {
            "waypoint_id": route_pos + 1,
            "stop_name": station_name,
            "region": station.get("station_type", ""),
            "latitude": lat,
            "longitude": lon,
            "timestamp": pd.Timestamp.now().isoformat(),
            "wind_speed_ms": weather_row.get("wind_speed_avg_ms") if weather_row is not None else None,
            "wind_gust_ms": weather_row.get("wind_gust_max_ms") if weather_row is not None else None,
            "temperature_c": weather_row.get("air_temp_c") if weather_row is not None else None,
            "precipitation_mm": weather_row.get("precipitation_mm") if weather_row is not None else 0,
            "road_status": weather_row.get("road_status", "Open") if weather_row is not None else "Open",
            "fuel_price_isk": fuel_info.get("price") or fuel_info.get("regular_price"),
            "fuel_brand": fuel_info.get("company", ""),
            "campsite_fee_isk": 2500,
            "campsite_availability": "Available",
            "daylight_hours": 12,
            "air_temp_c": weather_row.get("air_temp_c") if weather_row is not None else None,
            "air_temp_max_c": weather_row.get("air_temp_max_c") if weather_row is not None else None,
            "air_temp_min_c": weather_row.get("air_temp_min_c") if weather_row is not None else None,
            "relative_humidity_pct": weather_row.get("relative_humidity_pct") if weather_row is not None else None,
            "wind_speed_avg_ms": weather_row.get("wind_speed_avg_ms") if weather_row is not None else None,
            "wind_speed_max_ms": weather_row.get("wind_speed_max_ms") if weather_row is not None else None,
            "wind_gust_max_ms": weather_row.get("wind_gust_max_ms") if weather_row is not None else None,
            "wind_dir_deg": weather_row.get("wind_dir_deg") if weather_row is not None else None,
            "wind_dir_cardinal": weather_row.get("wind_dir_cardinal") if weather_row is not None else None,
            "sea_level_pressure_hpa": weather_row.get("sea_level_pressure_hpa") if weather_row is not None else None,
            "road_surface_temp_c": weather_row.get("road_surface_temp_c") if weather_row is not None else None,
            "dew_point_c": weather_row.get("dew_point_c") if weather_row is not None else None,
            "station_id": weather_row.get("station_id") if weather_row is not None else station.get("station_id"),
            "station_name": station_name,
            "observation_time_utc": weather_row.get("observation_time_utc") if weather_row is not None else pd.Timestamp.now().isoformat(),
            "matched_point": station_name,
            "distance_km": distance_km,
            # Forecast fields (what weather will be like at ETA)
            "forecast_temp_c": forecast_row.get("forecast_temp_c") if forecast_row is not None else None,
            "forecast_wind_dir_deg": forecast_row.get("forecast_wind_dir_deg") if forecast_row is not None else None,
            "forecast_wind_dir_cardinal": forecast_row.get("forecast_wind_dir_cardinal") if forecast_row is not None else None,
            "forecast_weather_type": forecast_row.get("forecast_weather_type") if forecast_row is not None else None,
            "forecast_valid_time_utc": forecast_row.get("forecast_valid_time_utc") if forecast_row is not None else None,
            "eta_utc": forecast_row.get("eta_utc") if forecast_row is not None else None,
            "distance_from_origin_km": forecast_row.get("distance_from_origin_km") if forecast_row is not None else None,
            "time_diff_hours": forecast_row.get("time_diff_hours") if forecast_row is not None else None,
        }
        records.append(record)
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("waypoint_id").reset_index(drop=True)
    return df


def _create_fuel_stations_df(fuel_stations: List[Dict], directions: Dict = None) -> pd.DataFrame:
    """Create fuel stations dataframe with distance from origin for route ordering."""
    if not fuel_stations:
        return pd.DataFrame()
    
    records = []
    for fs in fuel_stations:
        base_price = fs.get("price") or fs.get("regular_price")
        discount_price = fs.get("discount_price")
        
        # Use route distance if available (from get_fuel_price_at_route with waypoint_distances)
        # Fall back to straight-line distance calculation if not
        distance_from_origin_km = fs.get("route_distance_km", 0.0)
        if distance_from_origin_km == 0.0 and directions:
            # Try to calculate from near_waypoint coordinates
            near_lat = fs.get("near_waypoint_lat")
            near_lon = fs.get("near_waypoint_lon")
            if near_lat and near_lon:
                # Use accumulated distance from the route sample points
                from src.ingestion.vedur_station_mapper import haversine_km
                # Simple approximation: use distance from first waypoint (origin)
                route_waypoints = _directions_to_waypoints(directions)
                if route_waypoints:
                    origin_lat, origin_lon = route_waypoints[0]
                    distance_from_origin_km = round(haversine_km(origin_lat, origin_lon, near_lat, near_lon), 2)
        
        records.append({
            "station_name": fs.get("station_name", ""),
            "company": fs.get("company", ""),
            "price_isk": base_price,
            "regular_price_isk": base_price,
            "discount_price_isk": discount_price,
            "distance_km": fs.get("distance_km", 0),
            "distance_from_origin_km": distance_from_origin_km,
        })
    
    df = pd.DataFrame(records)
    # Sort by distance from origin (route order)
    if not df.empty and "distance_from_origin_km" in df.columns:
        df = df.sort_values("distance_from_origin_km").reset_index(drop=True)
    return df
def get_route_data(
    origin: str,
    destination: str,
    waypoints: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    force_refresh: bool = False
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    route_key = _generate_route_key(origin, destination, waypoints)
    structure_path, weather_path, fuel_path, metadata_path = _get_cache_paths(route_key)
    
    structure = None
    if not force_refresh and _is_cache_valid(metadata_path, STRUCTURE_TTL):
        structure = _load_route_structure(structure_path)
    
    if structure is None:
        print(f"Fetching route structure for: {route_key}")
        directions = get_directions(origin=origin, destination=destination, waypoints=waypoints, api_key=api_key)
        if not directions:
            raise ValueError("Failed to get directions from Google Maps API")
        
        mapper = VedurRouteWeatherMapper()
        matched_stations, weather_df, forecast_df = mapper.get_route_weather_pipeline_with_forecast(directions)
        route_order = {s["station_name"]: i for i, s in enumerate(matched_stations)}
        
        structure = {
            "directions": directions,
            "matched_stations": matched_stations,
            "route_order": route_order
        }
        _save_route_structure(structure_path, directions, matched_stations, route_order)
        _save_metadata(metadata_path, "structure")
    else:
        print(f"Using cached route structure: {route_key}")
        directions = structure["directions"]
        matched_stations = structure["matched_stations"]
        route_order = structure["route_order"]
    
    weather_df = pd.DataFrame()
    if not force_refresh and _is_cache_valid(metadata_path, WEATHER_TTL) and weather_path.exists():
        try:
            weather_df = pd.read_parquet(weather_path)
            print(f"Using cached weather data (age < 1hr): {route_key}")
        except Exception:
            pass
    
    if weather_df.empty:
        print(f"Fetching live weather data: {route_key}")
        mapper = VedurRouteWeatherMapper()
        _, weather_df = mapper.get_route_weather_pipeline(directions)
        if not weather_df.empty:
            weather_df.to_parquet(weather_path, index=False)
            _save_metadata(metadata_path, "weather")
    
    fuel_df = pd.DataFrame()
    if not force_refresh and _is_cache_valid(metadata_path, FUEL_TTL) and fuel_path.exists():
        try:
            fuel_df = pd.read_parquet(fuel_path)
            print(f"Using cached fuel data (age < 6hr): {route_key}")
        except Exception:
            pass
    
    if fuel_df.empty:
        print(f"Fetching live fuel data: {route_key}")
        route_waypoints_with_dist = _directions_to_waypoints_with_distance(directions)
        route_waypoints = [(lat, lng) for lat, lng, _ in route_waypoints_with_dist]
        waypoint_distances = [dist for _, _, dist in route_waypoints_with_dist]
        fuel_stations = get_fuel_price_at_route(route_waypoints, max_distance_km=30.0, waypoint_distances=waypoint_distances)
        fuel_df = _create_fuel_stations_df(fuel_stations, directions)
        if not fuel_df.empty:
            fuel_df.to_parquet(fuel_path, index=False)
            _save_metadata(metadata_path, "fuel")
    else:
        fuel_stations = fuel_df.to_dict("records")
    
    telemetry_df = _create_telemetry_from_live_data(
        directions, structure["matched_stations"], weather_df, fuel_stations, structure["route_order"],
        forecast_df
    )
    
    print(f"Route data ready: {route_key} ({len(telemetry_df)} stations, {len(fuel_df)} fuel stops)")
    return telemetry_df, fuel_df


def clear_route_cache(route_key: Optional[str] = None) -> int:
    import shutil
    if route_key:
        route_dir = CACHE_DIR / route_key
        if route_dir.exists():
            shutil.rmtree(route_dir)
            return 1
        return 0
    else:
        count = 0
        for item in CACHE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
                count += 1
        return count


def list_cached_routes() -> List[Dict]:
    routes = []
    for route_dir in CACHE_DIR.iterdir():
        if route_dir.is_dir():
            structure_path = route_dir / "route_structure.json"
            weather_path = route_dir / "weather_live.parquet"
            fuel_path = route_dir / "fuel_live.parquet"
            metadata_path = route_dir / "metadata.json"
            
            if structure_path.exists():
                try:
                    with open(structure_path) as f:
                        structure = json.load(f)
                    
                    meta = {}
                    if metadata_path.exists():
                        with open(metadata_path) as f:
                            meta = json.load(f)
                    
                    routes.append({
                        "route_key": route_dir.name,
                        "stations": len(structure.get("matched_stations", [])),
                        "fuel_stops": len(pd.read_parquet(fuel_path)) if fuel_path.exists() else 0,
                        "created": pd.Timestamp(meta.get("structure_updated", 0), unit="s").isoformat() if meta.get("structure_updated") else "Unknown",
                        "weather_age_hr": round((time.time() - meta.get("weather_updated", time.time())) / 3600, 1) if meta.get("weather_updated") else None,
                        "fuel_age_hr": round((time.time() - meta.get("fuel_updated", time.time())) / 3600, 1) if meta.get("fuel_updated") else None,
                        "stops": [s.get("station_name", "") for s in structure.get("matched_stations", [])]
                    })
                except Exception:
                    pass
    return routes
