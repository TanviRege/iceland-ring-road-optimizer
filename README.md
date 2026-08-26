# Iceland Ring Road Optimizer

Optimize your Iceland Ring Road (Route 1) trip with live weather, road conditions, and fuel prices.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure your API key
Create a `.env` file in the project root with your own Google Maps API key:
```bash
GOOGLE_MAPS_API_KEY=your_key_here
```
> Note: never commit `.env` - it is git-ignored. Use your own key to stay within the API terms.

> Tip: set `DEFAULT_MAPS_URL` in `.env` to pre-fill the app with your favorite route.

### 3. Run the Streamlit app
```bash
streamlit run app.py
```
Opens at http://localhost:8501.

**In the app:**
1. Paste any Google Maps **directions** URL — a long `/maps/dir/…` link **or** the short share link from the Google Maps **Share → Copy link** button (e.g. `https://maps.app.goo.gl/…`).
2. Click **Fetch directions** to load the route, distance, and drive time.
3. Weather stations are matched automatically in route order and shown with live + forecast conditions.
4. Review the route-ordered dashboard: risk heatmap, temperature, wind, weather alerts, and fuel prices.

### 4. Run tests
```bash
pytest tests/ -q
```
URL parsing, validation, backend detection, and route-to-station ordering.

### 5. Use the notebook
```bash
jupyter notebook Test_Code.ipynb
```
The notebook reuses the same dynamic URL input (`input()` prompt in Jupyter).

## Project Structure

```
app.py                          # Streamlit front-end
src/
  interface/  maps_url_interface.py   directions.py
  ingestion/  vedur_station_mapper.py route_cache.py
  data_sources/ fuel_price_fetcher.py
  sql/        ring_road_analytics.sql
tests/        test_map_route_parser.py
Test_Code.ipynb                 # Analysis notebook
data/                          # Runtime caches & generated files (git-ignored)
```

## Key Features

- **Dynamic URL input** - no hardcoded Google Maps links; paste any route at runtime
- **Runtime-adaptive** - `st.text_input` in Streamlit, `input()` in notebook/CLI
- **Validation before API call** - catches bad URLs early with clear messages
- **Vedur.is weather** - 15 km spatial sampling, live + forecast-at-ETA, route-ordered stations
- **Fuel prices** - live from the Gasvaktin CDN
- **Camper-van safety** - risk thresholds tuned for a medium panel-van camper (Renault Trafic class)

## Data sources

- **Google Maps Directions & Geocoding API** - route geometry and waypoints (requires your own API key)
- **Icelandic Meteorological Office (Vedur.is)** - live observations & forecasts via their public XML endpoint
- **Gasvaktin** - real-time Icelandic fuel prices (MIT-licensed open data)

## Notes for reviewers

- The Streamlit app is `app.py`; run it with `streamlit run app.py`.
- Weather data is fetched from the lib XML endpoints; forecasts are matched to your estimated arrival time (ETA) at each station.
- The bundled `data/` folder and `.env` key are intentionally git-ignored for privacy and to avoid committing API keys or local caches.