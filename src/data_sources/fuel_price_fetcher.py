"""
Fuel Price Fetcher Module for Iceland Ring Road Optimizer

Connects to the Gasvaktin project (https://github.com/gasvaktin/gasvaktin) 
to fetch real-time fuel prices from Icelandic petrol stations.

Data Source: Gasvaktin - Automated price lookup for petrol stations in Iceland
License: MIT License (https://github.com/gasvaktin/gasvaktin)

Usage:
    from src.data_sources.fuel_price_fetcher import get_nearest_fuel_stations, find_closest_station
    
    # Get fuel stations sorted by distance from a route point
    stations = get_nearest_fuel_stations(lat=63.5, lon=-19.5, max_distance_km=50)
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Path to the gasvaktin submodule data files
GASVACTIN_DATA_PATH = Path(__file__).parent / "gasvaktin" / "vaktin"


class FuelPriceFetcher:
    """Fetch and query Icelandic fuel station prices from Gasvaktin data."""

    def __init__(self, data_file: Optional[str] = None):
        """
        Initialize the fuel price fetcher.

        Args:
            data_file: Path to gas price JSON file. Defaults to gasvaktin submodule data.
        """
        self.data_file = data_file or GASVACTIN_DATA_PATH / "gas.json"
        self._stations_cache: Optional[List[Dict]] = None
        self._last_updated: Optional[str] = None

    def _load_stations(self) -> List[Dict]:
        """Load stations from cache or file."""
        if self._stations_cache is None:
            if not self.data_file.exists():
                logger.error(f"Fuel price data file not found: {self.data_file}")
                logger.error("Ensure the gasvaktin submodule is initialized: git submodule update --init --recursive")
                return []

            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._stations_cache = data.get('stations', [])
                self._last_updated = "loaded from cache"
                logger.info(f"Loaded {len(self._stations_cache)} fuel stations from Gasvaktin")
            except Exception as e:
                logger.error(f"Failed to load fuel price data: {e}")
                self._stations_cache = []

        return self._stations_cache

    def refresh_data(self, submodule_path: str = None):
        """Clear cache to force reload of latest data."""
        self._stations_cache = None
        if submodule_path:
            self.data_file = Path(submodule_path) / "vaktin" / "gas.json"

    def get_all_stations(self) -> List[Dict]:
        """
        Get all fuel stations with their prices.

        Returns:
            List of station dictionaries with price data
        """
        return self._load_stations()

    def get_nearest_stations(
        self,
        lat: float,
        lon: float,
        max_distance_km: float = 50.0,
        limit: int = 10
    ) -> List[Dict]:
        """
        Find the nearest fuel stations to a given coordinate.

        Args:
            lat: Latitude of the reference point
            lon: Longitude of the reference point
            max_distance_km: Maximum distance in kilometers to search
            limit: Maximum number of stations to return

        Returns:
            List of station dictionaries sorted by distance, with distance_km added
        """
        from src.ingestion.vedur_station_mapper import haversine_km

        stations = self._load_stations()
        if not stations:
            return []

        # Calculate distance for each station
        stations_with_distance = []
        for station in stations:
            geo = station.get('geo', {})
            station_lat = geo.get('lat')
            station_lon = geo.get('lon')

            if station_lat is None or station_lon is None:
                continue

            distance = haversine_km(lat, lon, station_lat, station_lon)

            if distance <= max_distance_km:
                station_with_dist = {
                    **station,
                    'distance_km': round(distance, 2)
                }
                stations_with_distance.append(station_with_dist)

        # Sort by distance
        stations_with_distance.sort(key=lambda x: x['distance_km'])

        return stations_with_distance[:limit]

    def get_cheapest_stations(
        self,
        lat: float,
        lon: float,
        fuel_type: str = 'bensin95',
        max_distance_km: float = 50.0,
        include_discount: bool = True
    ) -> List[str]:
        """
        Find the cheapest fuel stations nearby.

        Args:
            lat: Latitude of the reference point
            lon: Longitude of the reference point
            fuel_type: 'bensin95' or 'diesel'
            max_distance_km: Maximum search distance
            include_discount: If True, consider discount prices when available

        Returns:
            List of station dictionaries sorted by price (cheapest first)
        """
        stations = self.get_nearest_stations(lat, lon, max_distance_km)

        def get_price(station: Dict) -> Optional[float]:
            base_price_key = fuel_type
            discount_key = f"{fuel_type}_discount"

            base_price = station.get(base_price_key)
            discount_price = station.get(discount_key)

            if include_discount and discount_price is not None:
                return discount_price
            elif base_price is not None:
                return base_price
            return None

        # Filter stations with valid prices and sort by price
        priced_stations = []
        for station in stations:
            price = get_price(station)
            if price is not None:
                priced_stations.append({
                    'station_name': station.get('name'),
                    'company': station.get('company'),
                    'fuel_type': fuel_type,
                    'price': price,
                    'discount_price': station.get(f"{fuel_type}_discount") if include_discount else None,
                    'distance_km': station.get('distance_km'),
                    'lat': station.get('geo', {}).get('lat'),
                    'lon': station.get('geo', {}).get('lon'),
                    'key': station.get('key')
                })

        priced_stations.sort(key=lambda x: x['price'])
        return priced_stations


# Convenience functions for quick access
_fetcher_instance: Optional[FuelPriceFetcher] = None


def get_fetcher() -> FuelPriceFetcher:
    """Get or create the singleton fetcher instance."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = FuelPriceFetcher()
    return _fetcher_instance


def get_nearest_fuel_stations(
    lat: float,
    lon: float,
    max_distance_km: float = 50.0,
    limit: int = 10
) -> List[Dict]:
    """Find nearest fuel stations to a coordinate."""
    return get_fetcher().get_nearest_stations(lat, lon, max_distance_km, limit)


def find_closest_station(
    lat: float,
    lon: float,
    fuel_type: str = 'bensin95'
) -> Optional[Dict]:
    """Find the single closest fuel station with the given fuel type."""
    nearby = get_fetcher().get_nearest_stations(lat, lon, max_distance_km=10.0, limit=1)
    if nearby:
        station = nearby[0]
        return {
            'station_name': station.get('name'),
            'company': station.get('company'),
            'bensin95': station.get('bensin95'),
            'bensin95_discount': station.get('bensin95_discount'),
            'diesel': station.get('diesel'),
            'diesel_discount': station.get('diesel_discount'),
            'distance_km': station.get('distance_km'),
            'lat': station.get('geo', {}).get('lat'),
            'lon': station.get('geo', {}).get('lon'),
            'key': station.get('key')
        }
    return None


def get_fuel_price_at_route(
    waypoints: List[Tuple[float, float]],
    fuel_type: str = 'bensin95',
    max_distance_km: float = 30.0
) -> List[Dict]:
    """
    Find fuel stations near a route defined by waypoints.

    Args:
        waypoints: List of (lat, lon) tuples defining the route
        fuel_type: 'bensin95' or 'diesel'
        max_distance_km: Maximum distance from any route point

    Returns:
        List of fuel stations along the route with metadata
    """
    all_stations = []
    seen_keys = set()

    for waypoint in waypoints:
        lat, lon = waypoint
        stations = get_fetcher().get_nearest_stations(lat, lon, max_distance_km, limit=5)

        for station in stations:
            key = station.get('key', '')
            if key not in seen_keys:
                seen_keys.add(key)
                base_price = station.get(fuel_type)
                discount_price = station.get(f"{fuel_type}_discount")
                effective_price = discount_price if discount_price and discount_price > 0 else base_price

                route_station = {
                    'station_name': station.get('name'),
                    'company': station.get('company'),
                    'price': effective_price,
                    'regular_price': base_price,
                    'discount_price': discount_price,
                    'distance_km': station.get('distance_km'),
                    'lat': station.get('geo', {}).get('lat'),
                    'lon': station.get('geo', {}).get('lon'),
                    'key': key,
                    'near_waypoint_lat': lat,
                    'near_waypoint_lon': lon
                }
                all_stations.append(route_station)

    # Sort by price
    all_stations.sort(key=lambda x: x.get('price', float('inf')) if x.get('price') else float('inf'))
    return all_stations


if __name__ == "__main__":
    # Demo: Show nearest fuel stations to Reykjavik
    import sys

    logging.basicConfig(level=logging.INFO)

    # Reykjavik coordinates
    reykjavik_lat, reykjavik_lon = 64.1466, -21.9426

    print("Fetching fuel stations near Reykjavik...")
    stations = get_nearest_fuel_stations(reykjavik_lat, reykjavik_lon, max_distance_km=50)

    if stations:
        print(f"\nFound {len(stations)} fuel stations within 50km of Reykjavik:")
        print("-" * 80)
        for s in stations[:5]:
            name = s.get('name', 'Unknown')
            company = s.get('company', 'Unknown')
            price = s.get('bensin95', 'N/A')
            discount = s.get('bensin95_discount', 'N/A')
            dist = s.get('distance_km', 'N/A')
            print(f"{name:40} | {company:12} | 95: {price:>7} ISK | {discount:>7} (disc) | {dist:>6} km")
    else:
        print("No stations found. Ensure gasvaktin submodule is initialized.")
        sys.exit(1)
