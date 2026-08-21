"""
Fuel Price Fetcher Module for Iceland Ring Road Optimizer

Dynamically fetches real-time fuel prices from the Gasvaktin project 
(https://github.com/gasvaktin/gasvaktin) via raw GitHub JSON CDN.

Data Source: Gasvaktin - Automated price lookup for petrol stations in Iceland
License: MIT License (https://github.com/gasvaktin/gasvaktin)

Usage:
    from src.data_sources.fuel_price_fetcher import get_nearest_fuel_stations, find_closest_station
    
    # Get fuel stations sorted by distance from a route point
    stations = get_nearest_fuel_stations(lat=63.5, lon=-19.5, max_distance_km=50)
"""

import json
import os
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Raw GitHub CDN URL for gasvaktin data
GASVACTIN_URL = "https://raw.githubusercontent.com/gasvaktin/gasvaktin/master/vaktin/gas.json"
CACHE_DIR = Path(__file__).parent.parent.parent / "data"
CACHE_FILE = CACHE_DIR / "gas_price_cache.json"


class FuelPriceFetcher:
    """Fetch and query Icelandic fuel station prices directly from online Gasvaktin CDN."""
    
    def __init__(self, url: str = GASVACTIN_URL, cache_file: Path = CACHE_FILE):
        self.url = url
        self.cache_file = cache_file
        self._stations_cache: Optional[List[Dict]] = None
        
    def _load_stations(self, force_refresh: bool = False) -> List[Dict]:
        """Load stations from memory cache, online CDN, or local fallback file cache."""
        if self._stations_cache is not None and not force_refresh:
            return self._stations_cache
            
        # Try fetching online first
        try:
            logger.info(f"Fetching latest fuel prices online from: {self.url}")
            response = requests.get(self.url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            stations = data.get('stations', [])
            if stations:
                self._stations_cache = stations
                # Save to local fallback cache
                try:
                    os.makedirs(self.cache_file.parent, exist_ok=True)
                    with open(self.cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    logger.info("Successfully updated local fuel price cache.")
                except Exception as write_err:
                    logger.warning(f"Could not write local fallback cache: {write_err}")
                return self._stations_cache
        except Exception as err:
            logger.warning(f"Failed to fetch online fuel prices ({err}). Trying local fallback cache...")
            
        # Fallback to local cache if offline or error
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._stations_cache = data.get('stations', [])
                logger.info(f"Loaded {len(self._stations_cache)} stations from local fallback cache.")
                return self._stations_cache
            except Exception as read_err:
                logger.error(f"Failed to load local fallback cache: {read_err}")
                
        logger.error("No fuel price data available online or in local cache.")
        return []
    
    def refresh_data(self) -> List[Dict]:
        """Force fetch fresh data from online CDN."""
        return self._load_stations(force_refresh=True)
    
    def get_all_stations(self) -> List[Dict]:
        """Get all fuel stations with their prices."""
        return self._load_stations()
    
    def get_nearest_stations(
        self, 
        lat: float, 
        lon: float, 
        max_distance_km: float = 50.0, 
        limit: int = 10
    ) -> List[Dict]:
        """Find the nearest fuel stations to a given coordinate."""
        from src.ingestion.vedur_station_mapper import haversine_km
        
        stations = self._load_stations()
        if not stations:
            return []
        
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
        
        stations_with_distance.sort(key=lambda x: x['distance_km'])
        return stations_with_distance[:limit]
    
    def get_cheapest_stations(
        self,
        lat: float,
        lon: float,
        fuel_type: str = 'bensin95',
        max_distance_km: float = 50.0,
        include_discount: bool = True
    ) -> List[Dict]:
        """Find the cheapest fuel stations nearby, sorted by price."""
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
        
        priced_stations = []
        for station in stations:
            price = get_price(station)
            if price is not None:
                raw_name = station.get('name', '')
                company = station.get('company', '')
                if company and raw_name and company.lower() not in raw_name.lower():
                    precise_name = f"{company} {raw_name}"
                else:
                    precise_name = raw_name
                priced_stations.append({
                    'station_name': precise_name,
                    'company': company,
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


# Convenience functions
_fetcher_instance: Optional[FuelPriceFetcher] = None

def get_fetcher() -> FuelPriceFetcher:
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
        raw_name = station.get('name', '')
        company = station.get('company', '')
        if company and raw_name and company.lower() not in raw_name.lower():
            precise_name = f"{company} {raw_name}"
        else:
            precise_name = raw_name
        return {
            'station_name': precise_name,
            'company': company,
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
    """Find fuel stations near a route defined by waypoints."""
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
                
                # Create more precise station name by combining company + name
                raw_name = station.get('name', '')
                company = station.get('company', '')
                if company and raw_name and company.lower() not in raw_name.lower():
                    precise_name = f"{company} {raw_name}"
                else:
                    precise_name = raw_name
                
                route_station = {
                    'station_name': precise_name,
                    'company': company,
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
    
    all_stations.sort(key=lambda x: x.get('price', float('inf')) if x.get('price') else float('inf'))
    return all_stations


if __name__ == "__main__":
    # Demo: Fetch stations near Reykjavik
    logging.basicConfig(level=logging.INFO)
    reykjavik_lat, reykjavik_lon = 64.1466, -21.9426
    
    print("Fetching dynamic fuel prices from Gasvaktin online...")
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
        print("Could not retrieve online prices.")
