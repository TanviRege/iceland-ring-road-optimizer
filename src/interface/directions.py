"""
Google Maps Directions API helpers.

These are extracted from the notebook so the Streamlit app (``app.py``) and any
other entry point can reuse them without duplicating logic. The notebook retains
its own inline copies; both implementations are kept intentionally identical.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except Exception:  # pragma: no cover
    pass


def geocode_place(place_name: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Convert a place name to lat/lon using the Google Geocoding API."""
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or api_key == "your_google_maps_api_key_here":
        print("\u274c No valid Google Maps API key set in .env")
        return None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": place_name, "key": api_key}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    if data["status"] == "OK" and data["results"]:
        loc = data["results"][0]["geometry"]["location"]
        return {
            "place_name": place_name,
            "formatted_address": data["results"][0]["formatted_address"],
            "lat": loc["lat"],
            "lng": loc["lng"],
            "place_id": data["results"][0]["place_id"],
        }
    print(f"\u274c Geocoding failed: {data['status']}")
    return None


def get_directions(
    origin: str,
    destination: str,
    waypoints: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    mode: str = "driving",
) -> Optional[Dict[str, Any]]:
    """Get driving directions using the Google Directions API.

    ``origin`` / ``destination`` / ``waypoints`` may be either free-text place
    names (e.g. "Reykjavík, Iceland") or ``place_id:xxx`` strings.
    """
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or api_key == "your_google_maps_api_key_here":
        print("\u274c No valid API key set in .env")
        return None

    url = "https://maps.googleapis.com/maps/api/directions/json"
    params: Dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "key": api_key,
    }
    if waypoints:
        params["waypoints"] = "|".join(f"via:{wp}" for wp in waypoints)

    r = requests.get(url, params=params, timeout=15)
    data = r.json()

    if data["status"] != "OK" or not data["routes"]:
        print(f"\u274c Directions failed: {data['status']}")
        if "error_message" in data:
            print(f"   {data['error_message']}")
        return None

    route = data["routes"][0]
    legs = []
    total_distance_m = 0
    total_duration_s = 0

    for i, leg in enumerate(route["legs"]):
        legs.append({
            "leg_index": i,
            "start_address": leg["start_address"],
            "end_address": leg["end_address"],
            "start_location": leg["start_location"],
            "end_location": leg["end_location"],
            "distance_m": leg["distance"]["value"],
            "distance_text": leg["distance"]["text"],
            "duration_s": leg["duration"]["value"],
            "duration_text": leg["duration"]["text"],
            "steps_count": len(leg["steps"]),
            "steps": [
                {"start_location": s.get("start_location"), "end_location": s.get("end_location")}
                for s in leg["steps"]
            ],
        })
        total_distance_m += leg["distance"]["value"]
        total_duration_s += leg["duration"]["value"]

    return {
        "total_distance_km": round(total_distance_m / 1000, 2),
        "total_duration_hours": round(total_duration_s / 3600, 2),
        "total_distance_text": f"{total_distance_m / 1000:.1f} km",
        "total_duration_text": f"{total_duration_s / 3600:.1f} hours",
        "legs": legs,
        "overview_polyline": route["overview_polyline"]["points"],
    }


# --------------------------------------------------------------------------- #
# Ring Road waypoint injection
# --------------------------------------------------------------------------- #

# Major towns along Iceland's Route 1 (Ring Road), clockwise from Reykjavík
# Each entry: (town_name, approx_cumulative_km_from_reykjavik)
RING_ROAD_TOWNS = [
    ("Reykjavík, Iceland", 0),
    ("Borgarnes, Iceland", 75),
    ("Bifröst, Iceland", 105),
    ("Laugarbakki, Iceland", 160),
    ("Blönduós, Iceland", 200),
    ("Varmahlíð, Iceland", 240),
    ("Akureyri, Iceland", 320),
    ("Húsavík, Iceland", 390),
    ("Mývatn, Iceland", 430),
    ("Egilsstaðir, Iceland", 580),
    ("Höfn, Iceland", 780),
    ("Kirkjubæjarklaustur, Iceland", 920),
    ("Vík í Mýrdal, Iceland", 1050),
    ("Selfoss, Iceland", 1180),
    ("Reykjavík, Iceland", 1332),  # Full loop
]


def _geocode_batch(places: List[str], api_key: str) -> Dict[str, Dict[str, float]]:
    """Batch geocode multiple places to get lat/lng for distance calculations."""
    results = {}
    for place in places:
        geo = geocode_place(place, api_key)
        if geo:
            results[place] = {"lat": geo["lat"], "lng": geo["lng"]}
    return results


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in km."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def inject_ring_road_waypoints(
    origin: str,
    destination: str,
    existing_waypoints: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    min_distance_km: float = 200.0,
) -> List[str]:
    """
    Inject Ring Road waypoints for long routes to ensure Google follows Route 1.
    
    This adds major towns as intermediate waypoints so Google Maps doesn't
    take shortcuts across the interior. Only activates for routes > min_distance_km.
    
    Parameters
    ----------
    origin : str
        Starting location (place name or place_id)
    destination : str
        Ending location
    existing_waypoints : list, optional
        Waypoints already in the route (will be preserved)
    api_key : str, optional
        Google Maps API key for geocoding
    min_distance_km : float
        Minimum route distance to trigger waypoint injection
        
    Returns
    -------
    list
        Combined waypoints (existing + injected Ring Road towns)
    """
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or api_key == "your_google_maps_api_key_here":
        return existing_waypoints or []
    
    # Geocode origin and destination to estimate direct distance
    geo_origin = geocode_place(origin, api_key)
    geo_dest = geocode_place(destination, api_key)
    
    if not geo_origin or not geo_dest:
        return existing_waypoints or []
    
    direct_km = _haversine_km(
        geo_origin["lat"], geo_origin["lng"],
        geo_dest["lat"], geo_dest["lng"]
    )
    
    # Only inject for long routes
    if direct_km < min_distance_km:
        return existing_waypoints or []
    
    # Determine direction (clockwise or counter-clockwise around Ring Road)
    # by finding which Ring Road town is closest to origin and destination
    all_towns = [t[0] for t in RING_ROAD_TOWNS]
    geo_towns = _geocode_batch(all_towns, api_key)
    
    def find_closest_town(geo_point):
        min_dist = float('inf')
        closest_idx = 0
        for i, town in enumerate(all_towns):
            if town in geo_towns:
                d = _haversine_km(
                    geo_point["lat"], geo_point["lng"],
                    geo_towns[town]["lat"], geo_towns[town]["lng"]
                )
                if d < min_dist:
                    min_dist = d
                    closest_idx = i
        return closest_idx
    
    origin_idx = find_closest_town(geo_origin)
    dest_idx = find_closest_town(geo_dest)
    
    # Determine direction: shorter path around the ring
    ring_length = RING_ROAD_TOWNS[-1][1]  # 1332 km
    
    # Clockwise distance
    if dest_idx >= origin_idx:
        cw_dist = RING_ROAD_TOWNS[dest_idx][1] - RING_ROAD_TOWNS[origin_idx][1]
    else:
        cw_dist = ring_length - (RING_ROAD_TOWNS[origin_idx][1] - RING_ROAD_TOWNS[dest_idx][1])
    
    # Counter-clockwise distance
    ccw_dist = ring_length - cw_dist
    
    # Choose direction with more towns (more detailed routing)
    if cw_dist <= ccw_dist:
        # Clockwise: origin_idx -> dest_idx
        if dest_idx > origin_idx:
            injected = [RING_ROAD_TOWNS[i][0] for i in range(origin_idx + 1, dest_idx)]
        else:
            # Wrap around
            injected = [RING_ROAD_TOWNS[i][0] for i in range(origin_idx + 1, len(RING_ROAD_TOWNS) - 1)]
            injected += [RING_ROAD_TOWNS[i][0] for i in range(0, dest_idx)]
    else:
        # Counter-clockwise: origin_idx -> dest_idx (backwards)
        if origin_idx > dest_idx:
            injected = [RING_ROAD_TOWNS[i][0] for i in range(origin_idx - 1, dest_idx, -1)]
        else:
            # Wrap around
            injected = [RING_ROAD_TOWNS[i][0] for i in range(origin_idx - 1, -1, -1)]
            injected += [RING_ROAD_TOWNS[i][0] for i in range(len(RING_ROAD_TOWNS) - 2, dest_idx, -1)]
    
    # Limit to max 23 waypoints (Google limit: 25 total - origin - dest)
    max_waypoints = 23
    if len(injected) > max_waypoints:
        # Sample evenly
        step = len(injected) / max_waypoints
        injected = [injected[int(i * step)] for i in range(max_waypoints)]
    
    # Combine with existing waypoints (existing first, then injected)
    combined = (existing_waypoints or []) + injected
    
    return combined
