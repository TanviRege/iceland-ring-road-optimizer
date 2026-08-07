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


__all__ = ["geocode_place", "get_directions"]
