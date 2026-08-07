# Iceland Ring Road Optimizer

Optimize your Iceland Ring Road (Route 1) trip with live weather, road conditions, and fuel prices.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure API key
Create `.env` in the project root with your Google Maps API key:
```bash
GOOGLE_MAPS_API_KEY=your_key_here
```
(Already present in this repo for development.)

### 3. Run the Streamlit app
```bash
streamlit run app.py
```
Opens at `http://localhost:8501`.

**In the app:**
1. Paste a Google Maps **directions** URL (Share → Copy link from maps.google.com).
2. The sample Ring Road route (Reykjavík → Skaftafell) is pre-filled as fallback.
3. Click **Fetch directions** to get distance, drive time, and a route map.
4. Click **Fetch live weather stations** to sample Vedur.is weather stations along the route.

### 4. Run tests
```bash
pytest tests/ -q
```
18 tests covering URL parsing, validation, and backend detection.

### 5. Use the notebook
```bash
jupyter notebook Test_Code.ipynb
```
Cell `feec54ce` now uses the same dynamic URL input (via `input()` prompt in Jupyter).

## Project Structure

```
app.py                          # Streamlit front-end
src/interface/
  maps_url_interface.py         # Runtime-adaptive URL input (Streamlit/Jupyter/CLI)
  directions.py                 # Google Directions API wrapper
src/ingestion/
  vedur_station_mapper.py       # Maps Google routes → Vedur weather stations
  fetch_iceland_data.py         # Weather/road/fuel ingestion
src/data_sources/
  fuel_price_fetcher.py         # Gasvaktin fuel prices
tests/
  test_map_route_parser.py      # URL parsing/validation tests
Test_Code.ipynb                 # Analysis notebook (patched for dynamic URL)
```

## Key Features

- **Dynamic URL input** — no hardcoded Google Maps links; paste any route at runtime
- **Runtime-adaptive** — `st.text_input` in Streamlit, `input()` in notebook/CLI
- **Validation before API call** — catches bad URLs early with clear messages
- **Vedur.is weather** — 15 km spatial sampling along the route
- **Fuel prices** — live from Gasvaktin CDN