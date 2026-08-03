"""
Iceland Ring Road Optimizer - Data Ingestion Module

Fetches live weather, road conditions, and fuel price data from Icelandic public APIs.
Sources:
- Icelandic Meteorological Office (api.vedur.is)
- Icelandic Road Administration (umferdin.is / IRCA)
- Open fuel price feeds
"""

import requests
import pandas as pd
from typing import Dict, List, Optional
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Iceland Ring Road major waypoints (Route 1)
RING_ROAD_WAYPOINTS = [
    {"waypoint_id": 1, "stop_name": "Reykjavík", "region": "Capital Region", "latitude": 64.1466, "longitude": -21.9426},
    {"waypoint_id": 2, "stop_name": "Borgarnes", "region": "West", "latitude": 64.5397, "longitude": -21.9171},
    {"waypoint_id": 3, "stop_name": "Akureyri", "region": "North", "latitude": 65.6835, "longitude": -18.0929},
    {"waypoint_id": 4, "stop_name": "Egilsstaðir", "region": "East", "latitude": 65.2667, "longitude": -14.3931},
    {"waypoint_id": 5, "stop_name": "Höfn", "region": "East", "latitude": 64.2500, "longitude": -15.2167},
    {"waypoint_id": 6, "stop_name": "Vík", "region": "South", "latitude": 63.4194, "longitude": -19.0136},
    {"waypoint_id": 7, "stop_name": "Kirkjubæjarklaustur", "region": "South", "latitude": 63.7848, "longitude": -18.0694},
    {"waypoint_id": 8, "stop_name": "Selfoss", "region": "South", "latitude": 63.9292, "longitude": -20.9842},
]

# Weather stations near waypoints (IMO stations)
WEATHER_STATIONS = {
    1: "reykjavik",  # Reykjavík area
    2: "borgarnes",  # Borgarnes area
    3: "akureyri",   # Akureyri area
    4: "egilsstadir", # Egilsstaðir area
    5: "hofn",       # Höfn area
    6: "vik",        # Vík area
    7: "kirkjubaejarklaustur", # Kirkjubæjarklaustur area
    8: "selfoss",    # Selfoss area
}

# Fuel station brands in Iceland
FUEL_BRANDS = ["N1", "Olís", "Orkan", "Atlantsolía", "Skeljungur"]


class IcelandDataFetcher:
    """Fetches live data from Icelandic public APIs."""
    
    def __init__(self):
        self.base_weather_url = "https://api.vedur.is/v1/weather/observations"
        self.base_road_url = "https://api.umferdin.is/roadconditions"
        
    def fetch_weather_observations(self, station_id: str) -> Optional[Dict]:
        """
        Fetch weather observations from Icelandic Meteorological Office.
        Note: api.vedur.is may require registration. This is a template.
        """
        try:
            url = f"{self.base_weather_url}/xml?station={station_id}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            # Parse XML response (vedur.is returns XML)
            # For now, return structured data
            logger.info(f"Fetched weather for station: {station_id}")
            return {"station": station_id, "timestamp": datetime.now().isoformat(), "data": response.text}
        except Exception as e:
            logger.warning(f"Failed to fetch weather for {station_id}: {e}")
            return None
    
    def fetch_road_conditions(self) -> Optional[List[Dict]]:
        """
        Fetch road conditions from Icelandic Road Administration.
        Note: umferdin.is API structure may vary. This is a template.
        """
        try:
            url = f"{self.base_road_url}/roadconditions"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            logger.info("Fetched road conditions")
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch road conditions: {e}")
            return None
    
    def generate_synthetic_telemetry(self) -> pd.DataFrame:
        """
        Generate realistic synthetic telemetry data for development and testing.
        This simulates what the live APIs would return.
        """
        import random
        import numpy as np
        
        # Base weather patterns by region
        region_weather = {
            "Capital Region": {"wind_base": 5, "gust_base": 8, "temp_base": 8},
            "West": {"wind_base": 7, "gust_base": 10, "temp_base": 7},
            "North": {"wind_base": 6, "gust_base": 9, "temp_base": 5},
            "East": {"wind_base": 9, "gust_base": 14, "temp_base": 4},
            "South": {"wind_base": 10, "gust_base": 16, "temp_base": 6},
        }
        
        records = []
        for wp in RING_ROAD_WAYPOINTS:
            region = wp["region"]
            weather = region_weather.get(region, {"wind_base": 7, "gust_base": 10, "temp_base": 7})
            
            # Add realistic variation
            wind_speed = max(0, np.random.normal(weather["wind_base"], 3))
            wind_gust = max(wind_speed, np.random.normal(weather["gust_base"], 4))
            
            # Road status based on weather
            if wind_gust > 20:
                road_status = "Impassable"
            elif wind_gust > 15:
                road_status = "Gravel / Ice"
            elif wind_gust > 10:
                road_status = "Caution Advised"
            else:
                road_status = "Open"
            
            # Fuel prices vary by region and brand (ISK per liter)
            base_price = 315 + (hash(wp["stop_name"]) % 15)  # 315-330 ISK/L
            fuel_price = round(base_price + random.uniform(-3, 3), 1)
            
            records.append({
                "waypoint_id": wp["waypoint_id"],
                "stop_name": wp["stop_name"],
                "region": region,
                "latitude": wp["latitude"],
                "longitude": wp["longitude"],
                "timestamp": datetime.now().isoformat(),
                "wind_speed_ms": round(wind_speed, 1),
                "wind_gust_ms": round(wind_gust, 1),
                "temperature_c": round(np.random.normal(weather["temp_base"], 3), 1),
                "precipitation_mm": round(max(0, np.random.exponential(2)), 1),
                "road_status": road_status,
                "fuel_price_isk": fuel_price,
                "fuel_brand": random.choice(FUEL_BRANDS),
                "campsite_fee_isk": random.randint(1500, 3500),
                "campsite_availability": random.choice(["Available", "Limited", "Full"]),
                "daylight_hours": round(12 + 6 * np.sin(2 * np.pi * (datetime.now().timetuple().tm_yday - 80) / 365), 1),
            })
        
        return pd.DataFrame(records)
    
    def fetch_all_data(self) -> pd.DataFrame:
        """
        Main method to fetch all data. Tries live APIs first, falls back to synthetic.
        """
        logger.info("Starting Iceland data fetch...")
        
        # Try live APIs (will likely fail without API keys)
        # weather_data = self.fetch_weather_observations("reykjavik")
        # road_data = self.fetch_road_conditions()
        
        # For now, use synthetic data that mimics real API structure
        df = self.generate_synthetic_telemetry()
        logger.info(f"Generated {len(df)} waypoint records")
        return df


def main():
    """CLI entry point for data ingestion."""
    fetcher = IcelandDataFetcher()
    df = fetcher.fetch_all_data()
    
    # Save to CSV for DuckDB consumption
    output_path = "data/iceland_raw_telemetry.csv"
    df.to_csv(output_path, index=False)
    logger.info(f"Saved raw data to {output_path}")
    
    # Also save as Parquet for better performance
    parquet_path = "data/iceland_raw_telemetry.parquet"
    df.to_parquet(parquet_path, index=False)
    logger.info(f"Saved raw data to {parquet_path}")
    
    return df


if __name__ == "__main__":
    main()