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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# JSON endpoint for stations list (this one works)
VEDUR_STATIONS_URL = "https://api.vedur.is/weather/stations"

# XML endpoints for observations and forecasts (these are the working ones)
VEDUR_XML_BASE_URL = "https://xmlweather.vedur.is/"
VEDUR_OBS_PARAMS = {"op_w": "xml", "type": "obs", "lang": "is", "view": "xml"}
VEDUR_FOREC_PARAMS = {"op_w": "xml", "type": "forec", "lang": "is", "view": "xml"}

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


# Icelandic wind direction abbreviations to degrees and cardinal names
_WIND_DIR_MAP = {
    "N": 0, "NNA": 22.5, "NA": 45, "ANA": 67.5,
    "A": 90, "ASA": 112.5, "SA": 135, "SSA": 157.5,
    "S": 180, "SSV": 202.5, "SV": 225, "VSV": 247.5,
    "V": 270, "VNV": 292.5, "NV": 315, "NNV": 337.5,
}

_CARDINAL_NAMES = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
]

def _wind_dir_to_degrees(dir_str: str) -> Optional[float]:
    """Convert Icelandic wind direction abbreviation to degrees."""
    return _WIND_DIR_MAP.get(dir_str.upper().strip())

def _wind_dir_to_cardinal(dir_str: str) -> str:
    """Convert Icelandic wind direction abbreviation to cardinal name."""
    deg = _wind_dir_to_degrees(dir_str)
    if deg is None:
        return dir_str
    idx = int((deg + 11.25) / 22.5) % 16
    return _CARDINAL_NAMES[idx]


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
        url = VEDUR_STATIONS_URL
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
        Returns points sorted by distance from origin.
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
                "lng": start_loc["lng"],
                "distance_from_origin_km": 0.0
            })

        # Process each leg & steps - track cumulative distance
        cumulative_dist_km = 0.0
        for leg_idx, leg in enumerate(legs):
            steps_data = leg.get("steps", [])
            if isinstance(steps_data, list):
                for step_idx, step in enumerate(steps_data):
                    if isinstance(step, dict) and "end_location" in step:
                        end_loc = step["end_location"]
                        if isinstance(end_loc, dict) and "lat" in end_loc and "lng" in end_loc:
                            step_dist = step.get("distance", {}).get("value", 0) / 1000.0  # meters to km
                            cumulative_dist_km += step_dist
                            sample_points.append({
                                "label": f"Leg {leg_idx + 1} Step {step_idx + 1}",
                                "lat": end_loc["lat"],
                                "lng": end_loc["lng"],
                                "distance_from_origin_km": round(cumulative_dist_km, 2)
                            })

        # Destination
        end_loc = legs[-1].get("end_location", {})
        if end_loc:
            sample_points.append({
                "label": f"Destination: {legs[-1].get('end_address', 'End')}",
                "lat": end_loc["lat"],
                "lng": end_loc["lng"],
                "distance_from_origin_km": round(cumulative_dist_km, 2)
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
                            "lng": pt[1],
                            "distance_from_origin_km": round(accumulated_dist, 2)
                        })
                        accumulated_dist = 0.0
                    last_pt = pt

        # Sort all sample points by distance from origin to ensure correct route order
        sample_points.sort(key=lambda p: p.get("distance_from_origin_km", 0.0))
        
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
                        "matched_route_label": pt["label"],
                        "distance_from_origin_km": pt.get("distance_from_origin_km", 0.0)
                    })

        return matched_stations

    def _parse_observation_xml(self, xml_text: str, station_id: int) -> Optional[Dict[str, Any]]:
        """Parse observation XML from xmlweather.vedur.is into a dict matching VEDUR_COLUMN_MAP."""
        try:
            root = ET.fromstring(xml_text)
            station_elem = root.find("station")
            if station_elem is None:
                return None
            
            obs = {"station_id": station_id}
            
            name_elem = station_elem.find("name")
            if name_elem is not None and name_elem.text:
                obs["name"] = name_elem.text
            
            time_elem = station_elem.find("time")
            if time_elem is not None and time_elem.text:
                obs["time"] = time_elem.text
            
            f_elem = station_elem.find("F")
            if f_elem is not None and f_elem.text:
                try:
                    obs["f"] = float(f_elem.text)
                except ValueError:
                    pass
            
            d_elem = station_elem.find("D")
            if d_elem is not None and d_elem.text:
                obs["d"] = d_elem.text
            
            fx_elem = station_elem.find("FX")
            if fx_elem is not None and fx_elem.text:
                try:
                    obs["fx"] = float(fx_elem.text)
                except ValueError:
                    pass
            
            fg_elem = station_elem.find("FG")
            if fg_elem is not None and fg_elem.text:
                try:
                    obs["fg"] = float(fg_elem.text)
                except ValueError:
                    pass
            
            t_elem = station_elem.find("T")
            if t_elem is not None and t_elem.text:
                try:
                    obs["t"] = float(t_elem.text.replace(",", "."))
                except ValueError:
                    pass
            
            w_elem = station_elem.find("W")
            if w_elem is not None and w_elem.text:
                obs["w"] = w_elem.text
            
            v_elem = station_elem.find("V")
            if v_elem is not None and v_elem.text:
                try:
                    obs["v"] = float(v_elem.text.replace(",", "."))
                except ValueError:
                    pass
            
            r_elem = station_elem.find("R")
            if r_elem is not None and r_elem.text:
                try:
                    obs["r"] = float(r_elem.text.replace(",", "."))
                except ValueError:
                    pass
            
            return obs
        except ET.ParseError as e:
            logger.error(f"❌ Failed to parse observation XML for station {station_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error parsing observation for station {station_id}: {e}")
            return None

    def fetch_weather_for_stations(self, station_ids: List[int]) -> pd.DataFrame:
        """
        Fetch latest hourly weather observations from Vedur XML API for a list of station IDs.
        """
        all_obs = []

        for st_id in station_ids:
            params = VEDUR_OBS_PARAMS.copy()
            params["ids"] = str(st_id)
            try:
                r = requests.get(VEDUR_XML_BASE_URL, params=params, timeout=10)
                r.raise_for_status()
                obs = self._parse_observation_xml(r.text, st_id)
                if obs:
                    all_obs.append(obs)
                    logger.info(f"✅ Fetched weather for Station {st_id} ({obs.get('name', '')})")
                else:
                    logger.warning(f"⚠️ Station {st_id}: No data returned")
            except Exception as e:
                logger.error(f"❌ Failed to fetch weather for station {st_id}: {e}")

        if not all_obs:
            return pd.DataFrame()

        df = pd.DataFrame(all_obs)
        df_renamed = df.rename(columns=VEDUR_COLUMN_MAP)
        return df_renamed

    def _parse_forecast_xml(self, xml_text: str, station_id: int) -> List[Dict[str, Any]]:
        """Parse forecast XML from xmlweather.vedur.is into a list of dicts."""
        forecasts = []
        try:
            root = ET.fromstring(xml_text)
            station_elem = root.find("station")
            if station_elem is None:
                return forecasts
            
            station_name = None
            name_elem = station_elem.find("name")
            if name_elem is not None and name_elem.text:
                station_name = name_elem.text
            
            for forecast_elem in station_elem.findall("forecast"):
                fc = {"station_id": station_id, "station_name": station_name}
                
                ftime_elem = forecast_elem.find("ftime")
                if ftime_elem is not None and ftime_elem.text:
                    fc["ftime"] = ftime_elem.text
                
                f_elem = forecast_elem.find("F")
                if f_elem is not None and f_elem.text:
                    try:
                        fc["f"] = float(f_elem.text)
                    except ValueError:
                        pass
                
                d_elem = forecast_elem.find("D")
                if d_elem is not None and d_elem.text:
                    fc["d"] = d_elem.text
                    # Also convert to cardinal direction
                    fc["d_txt"] = _wind_dir_to_cardinal(d_elem.text)
                
                t_elem = forecast_elem.find("T")
                if t_elem is not None and t_elem.text:
                    try:
                        fc["t"] = float(t_elem.text.replace(",", "."))
                    except ValueError:
                        pass
                
                w_elem = forecast_elem.find("W")
                if w_elem is not None and w_elem.text:
                    fc["w"] = w_elem.text
                
                forecasts.append(fc)
            
            return forecasts
        except ET.ParseError as e:
            logger.error(f"❌ Failed to parse forecast XML for station {station_id}: {e}")
            return forecasts
        except Exception as e:
            logger.error(f"❌ Error parsing forecast for station {station_id}: {e}")
            return forecasts

    def fetch_forecast_for_stations(self, station_ids: List[int]) -> pd.DataFrame:
        """
        Fetch weather forecasts from Vedur XML API for a list of station IDs.
        """
        all_forecasts = []

        for st_id in station_ids:
            params = VEDUR_FOREC_PARAMS.copy()
            params["ids"] = str(st_id)
            try:
                r = requests.get(VEDUR_XML_BASE_URL, params=params, timeout=10)
                r.raise_for_status()
                forecasts = self._parse_forecast_xml(r.text, st_id)
                if forecasts:
                    all_forecasts.extend(forecasts)
                    logger.info(f"✅ Fetched {len(forecasts)} forecast entries for Station {st_id}")
                else:
                    logger.warning(f"⚠️ Station {st_id}: No forecast data returned")
            except Exception as e:
                logger.error(f"❌ Failed to fetch forecast for station {st_id}: {e}")

        if not all_forecasts:
            return pd.DataFrame()

        df = pd.DataFrame(all_forecasts)
        rename_map = {}
        for col in df.columns:
            if col == "ftime":
                rename_map[col] = "forecast_valid_time_utc"
            elif col == "f":
                rename_map[col] = "forecast_wind_speed_ms"
            elif col == "d":
                rename_map[col] = "forecast_wind_dir_deg"
            elif col == "d_txt":
                rename_map[col] = "forecast_wind_dir_cardinal"
            elif col == "t":
                rename_map[col] = "forecast_temp_c"
            elif col == "w":
                rename_map[col] = "forecast_weather_type"
            elif col == "station_id":
                # Keep original station_id for matching
                rename_map[col] = "station_id"
            elif col == "station_name":
                rename_map[col] = "forecast_station_name"
            elif not col.startswith("forecast_"):
                rename_map[col] = f"forecast_{col}"
        df_renamed = df.rename(columns=rename_map)
        return df_renamed

    def calculate_station_etas(
        self, 
        directions_data: Dict[str, Any], 
        matched_stations: List[Dict[str, Any]],
        departure_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Calculate Estimated Time of Arrival (ETA) at each weather station along the route.
        
        Uses the route's total distance/duration and each station's distance along the route
        to proportionally calculate when the user will reach each station.
        """
        if departure_time is None:
            departure_time = datetime.now(timezone.utc)
        
        total_distance_km = directions_data.get("total_distance_km", 0)
        total_duration_hours = directions_data.get("total_duration_hours", 0)
        
        if total_distance_km == 0 or total_duration_hours == 0:
            logger.warning("Route has zero distance/duration, cannot calculate ETAs")
            return matched_stations
        
        # Calculate ETA for each station using pre-computed distance_from_origin_km from matched_stations
        for station in matched_stations:
            st_id = station["station_id"]
            dist_from_origin = station.get("distance_from_origin_km", 0)
            
            if total_distance_km > 0:
                progress_ratio = dist_from_origin / total_distance_km
                progress_ratio = max(0, min(1, progress_ratio))  # Clamp to [0, 1]
                eta_hours = total_duration_hours * progress_ratio
                eta_utc = departure_time + timedelta(hours=eta_hours)
                
                station["distance_from_origin_km"] = round(dist_from_origin, 1)
                station["eta_utc"] = eta_utc.isoformat()  # Store as ISO string for JSON serialization
                logger.info(f"  Station {st_id} ({station['station_name']}): {dist_from_origin:.1f}km from origin, ETA {eta_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            else:
                station["distance_from_origin_km"] = 0
                station["eta_utc"] = departure_time.isoformat()
        
        return matched_stations

    def match_forecasts_to_etas(
        self, 
        forecast_df: pd.DataFrame, 
        matched_stations: List[Dict[str, Any]]
    ) -> pd.DataFrame:
        """
        Match forecast data to each station's ETA by finding the forecast 
        entry closest in time to the station's ETA.
        
        Returns a DataFrame with one row per station containing forecast
        data for the time closest to ETA.
        """
        if forecast_df.empty or not matched_stations:
            return pd.DataFrame()
        
        matched_forecasts = []
        
        for station in matched_stations:
            st_id = station["station_id"]
            eta_str = station.get("eta_utc")
            
            if eta_str is None:
                continue
            
            # Parse eta string to datetime for comparison (ensure UTC timezone)
            try:
                eta = pd.Timestamp(eta_str, tz="UTC")
            except Exception:
                logger.warning(f"⚠️ Could not parse ETA for station {st_id}: {eta_str}")
                continue
            
            # Filter forecasts for this station
            station_forecasts = forecast_df[forecast_df["station_id"] == st_id].copy()
            
            if station_forecasts.empty:
                logger.warning(f"⚠️ No forecasts for station {st_id} ({station['station_name']})")
                continue
            
            # Convert forecast_valid_time_utc to datetime for comparison
            # XML timestamps are in UTC (Iceland uses UTC year-round)
            station_forecasts["forecast_valid_time_utc"] = pd.to_datetime(
                station_forecasts["forecast_valid_time_utc"], errors="coerce", utc=True
            )
            # Drop any rows where conversion failed
            station_forecasts = station_forecasts.dropna(subset=["forecast_valid_time_utc"])
            
            if station_forecasts.empty:
                logger.warning(f"⚠️ No valid forecast timestamps for station {st_id}")
                continue
            
            # Find forecast closest to ETA
            station_forecasts["time_diff"] = (station_forecasts["forecast_valid_time_utc"] - eta).abs()
            closest = station_forecasts.loc[station_forecasts["time_diff"].idxmin()]
            
            # Build result with station info + forecast at ETA
            result = {
                "station_id": st_id,
                "station_name": station["station_name"],
                "distance_from_origin_km": station.get("distance_from_origin_km", 0),
                "eta_utc": eta_str,  # Keep as ISO string for JSON serialization
                "forecast_valid_time_utc": closest.get("forecast_valid_time_utc"),
                "time_diff_hours": round(closest["time_diff"].total_seconds() / 3600, 2),
            }
            
            # Add all forecast fields
            for col in closest.index:
                if col.startswith("forecast_") and col not in result:
                    result[col] = closest[col]
            
            matched_forecasts.append(result)
        
        if not matched_forecasts:
            return pd.DataFrame()
        
        return pd.DataFrame(matched_forecasts)

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

    def get_route_weather_pipeline_with_forecast(self, directions_data: Dict[str, Any], max_distance_km: float = 30.0) -> Tuple[List[Dict[str, Any]], pd.DataFrame, pd.DataFrame]:
        """
        Full end-to-end pipeline with forecasts:
        1. Fetch active Vedur stations
        2. Map Google route sample points to nearest station IDs
        3. Calculate ETAs at each station
        4. Fetch live weather observations for matched stations
        5. Fetch forecasts for matched stations
        6. Match forecasts to ETAs
        
        Returns:
            matched_stations: List with ETA info
            df_weather: Live observations DataFrame
            df_forecast: Forecast at ETA DataFrame
        """
        self.fetch_active_stations()
        matched_stations = self.map_route_to_station_ids(directions_data, max_distance_km=max_distance_km)
        station_ids = [m["station_id"] for m in matched_stations]
        
        logger.info(f"Matched route to {len(station_ids)} weather stations: {station_ids}")
        
        # Calculate ETAs
        matched_stations = self.calculate_station_etas(directions_data, matched_stations)
        
        # Fetch live observations
        df_weather = self.fetch_weather_for_stations(station_ids)
        
        # Fetch forecasts
        df_forecast_all = self.fetch_forecast_for_stations(station_ids)
        
        # Match forecasts to ETAs
        df_forecast_at_eta = self.match_forecasts_to_etas(df_forecast_all, matched_stations)
        
        return matched_stations, df_weather, df_forecast_at_eta
