"""
Vedur Weather Station Mapper for Google Maps Routes

Maps Google Maps route data (origin, destination, waypoints, polyline)
to nearest active Icelandic Meteorological Office (Vedur.is) weather stations,
and fetches live weather observations along the route.
"""

import math
import logging
import requests
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VEDUR_BASE_URL = "https://api.vedur.is/weather"

# Column map for Vedur observations
VEDUR_COLUMN_MAP = {
    "station": "station_id",
    "name": "station_name",
    "time": "observation_time_utc",
    "year": "year",
    "month": "month",
    "day": "day",
    "hour": "hour_utc",
    "t": "air_temp_c",
    "tx": "air_temp_max_c",
    "tn": "air_temp_min_c",
    "rh": "relative_humidity_pct",
    "vp": "vapor_pressure_hpa",
    "td": "dew_point_c",
    "f": "wind_speed_avg_ms",
    "fx": "wind_speed_max_ms",
    "fg": "wind_gust_max_ms",
    "fgfx": "gust_factor",
    "d": "wind_dir_deg",
    "d_txt": "wind_dir_cardinal",
    "ps": "station_pressure_hpa",
    "p": "sea_level_pressure_hpa",
    "r": "precipitation_mm",
    "tg": "ground_temp_c",
    "t0": "road_surface_temp_c",
    "count_measurements": "measurement_count",
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def decode_polyline(polyline_str: str) -> List[Tuple[float, float]]:
    """Decode Google Maps encoded polyline into list of (lat, lng) tuples."""
    index, lat, lng = 0, 0, 0
    coordinates = []
    changes = {'latitude': 0, 'longitude': 0}
    
    while index < len(polyline_str):
        for unit in ['latitude', 'longitude']:
            shift, result = 0, 0
            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not byte >= 0x20:
                    break
            if result & 1:
                changes[unit] = ~(result >> 1)
            else:
                changes[unit] = result >> 1
        lat += changes['latitude']
        lng += changes['longitude']
        coordinates.append((lat / 1e5, lng / 1e5))
        
    return coordinates


class VedurRouteWeatherMapper:
    """Maps Google Maps routes to active Vedur stations & fetches weather data."""

    def __init__(self):
        self.stations_cache: List[Dict[str, Any]] = []

    def fetch_active_stations(self, station_types: List[str] = ["sj", "sk"]) -> List[Dict[str, Any]]:
        """Fetch list of all active weather stations from Vedur API."""
        url = f"{VEDUR_BASE_URL}/stations"
        params = {"active": "true"}
        
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            # Filter for requested station types (sj = automatic, sk = synop)
            self.stations_cache = [s for s in data if s.get("type") in station_types]
            logger.info(f"Loaded {len(self.stations_cache)} active Vedur stations ({', '.join(station_types)})")
            return self.stations_cache
        except Exception as e:
            logger.error(f"Failed to fetch active Vedur stations: {e}")
            return []

    def find_nearest_station(self, lat: float, lng: float, max_distance_km: float = 30.0) -> Optional[Tuple[Dict[str, Any], float]]:
        """Find the nearest active station to a (lat, lng) coordinate within max_distance_km."""
        if not self.stations_cache:
            self.fetch_active_stations()

        closest_station = None
        min_dist = float("inf")

        for st in self.stations_cache:
            st_lat = st.get("lat")
            st_lon = st.get("lon")
            if st_lat is None or st_lon is None:
                continue
            dist = haversine_km(lat, lng, st_lat, st_lon)
            if dist < min_dist:
                min_dist = dist
                closest_station = st

        if closest_station and min_dist <= max_distance_km:
            return closest_station, min_dist
        return None

    def extract_route_sample_points(self, directions_data: Dict[str, Any], sample_interval_km: float = 15.0) -> List[Dict[str, Any]]:
        """
        Extract sample coordinates along a Google Directions route.
        Includes origin, destination, step endpoints, and 15km sampled polyline points.
        """
        sample_points = []
        
        # 1. Check if legs exist
        legs = directions_data.get("legs", [])
        if not legs:
            return sample_points

        # Extract origin
        start_loc = legs[0].get("start_location", {})
        if start_loc:
            sample_points.append({
                "label": f"Origin: {legs[0].get('start_address', 'Start')}",
                "lat": start_loc["lat"],
                "lng": start_loc["lng"]
            })

        # Process each leg & steps
        for leg_idx, leg in enumerate(legs):
            steps_data = leg.get("steps", [])
            if isinstance(steps_data, list):
                for step_idx, step in enumerate(steps_data):
                    if isinstance(step, dict) and "end_location" in step:
                        end_loc = step["end_location"]
                        if isinstance(end_loc, dict) and "lat" in end_loc and "lng" in end_loc:
                            sample_points.append({
                                "label": f"Leg {leg_idx + 1} Step {step_idx + 1}",
                                "lat": end_loc["lat"],
                                "lng": end_loc["lng"]
                            })

        # Destination
        end_loc = legs[-1].get("end_location", {})
        if end_loc:
            sample_points.append({
                "label": f"Destination: {legs[-1].get('end_address', 'End')}",
                "lat": end_loc["lat"],
                "lng": end_loc["lng"]
            })

        # Polyline spatial sampling at 15km intervals along road
        overview_polyline = directions_data.get("overview_polyline")
        if overview_polyline:
            poly_points = decode_polyline(overview_polyline)
            if poly_points:
                accumulated_dist = 0.0
                last_pt = poly_points[0]
                
                for pt in poly_points[1:]:
                    dist = haversine_km(last_pt[0], last_pt[1], pt[0], pt[1])
                    accumulated_dist += dist
                    if accumulated_dist >= sample_interval_km:
                        sample_points.append({
                            "label": f"Polyline Sample (~{accumulated_dist:.1f}km)",
                            "lat": pt[0],
                            "lng": pt[1]
                        })
                        accumulated_dist = 0.0
                    last_pt = pt

        return sample_points

    def map_route_to_station_ids(self, directions_data: Dict[str, Any], max_distance_km: float = 30.0, sample_interval_km: float = 15.0) -> List[Dict[str, Any]]:
        """
        Map a Google route to an ordered list of unique Vedur station IDs with distance & metadata.
        """
        sample_points = self.extract_route_sample_points(directions_data, sample_interval_km=sample_interval_km)
        matched_stations = []
        seen_station_ids = set()

        for pt in sample_points:
            res = self.find_nearest_station(pt["lat"], pt["lng"], max_distance_km=max_distance_km)
            if res:
                st, dist = res
                st_id = st["station"]
                if st_id not in seen_station_ids:
                    seen_station_ids.add(st_id)
                    matched_stations.append({
                        "station_id": st_id,
                        "station_name": st.get("name"),
                        "station_lat": st.get("lat"),
                        "station_lon": st.get("lon"),
                        "station_type": st.get("type"),
                        "distance_from_route_pt_km": round(dist, 2),
                        "matched_route_label": pt["label"]
                    })

        return matched_stations

    def fetch_weather_for_stations(self, station_ids: List[int]) -> pd.DataFrame:
        """
        Fetch latest hourly AWS weather observations from Vedur API for a list of station IDs.
        """
        url = f"{VEDUR_BASE_URL}/observations/aws/hour/latest"
        all_obs = []

        for st_id in station_ids:
            params = {
                "station_id": st_id,
                "parameters": "all"
            }
            try:
                r = requests.get(url, params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data:
                    all_obs.extend(data)
                    logger.info(f"✅ Fetched weather for Station {st_id} ({data[0].get('name', '')})")
                else:
                    logger.warning(f"⚠️ Station {st_id}: No data returned")
            except Exception as e:
                logger.error(f"❌ Failed to fetch weather for station {st_id}: {e}")

        if not all_obs:
            return pd.DataFrame()

        df = pd.DataFrame(all_obs)
        df_renamed = df.rename(columns=VEDUR_COLUMN_MAP)
        return df_renamed

    def get_route_weather_pipeline(self, directions_data: Dict[str, Any], max_distance_km: float = 30.0) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
        """
        Full end-to-end pipeline:
        1. Fetch active Vedur stations
        2. Map Google route sample points to nearest station IDs
        3. Fetch live weather observations for matched stations
        """
        self.fetch_active_stations()
        matched_stations = self.map_route_to_station_ids(directions_data, max_distance_km=max_distance_km)
        station_ids = [m["station_id"] for m in matched_stations]
        
        logger.info(f"Matched route to {len(station_ids)} weather stations: {station_ids}")
        df_weather = self.fetch_weather_for_stations(station_ids)

        return matched_stations, df_weather
