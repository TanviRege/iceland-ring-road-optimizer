-- ============================================================================
-- Iceland Ring Road Optimizer - SQL Analytics Pipeline
-- 
-- This file contains the complete DuckDB SQL pipeline using:
-- - Multi-level CTEs for data transformation
-- - Window Functions: LAG, LEAD, SUM OVER, AVG OVER, NTILE
-- - Geospatial calculations for route segment distances
-- - Risk profiling and unit economics modeling
--
-- Run with: duckdb -c "$(cat src/sql/ring_road_analytics.sql)"
-- Or use via Python: duckdb.query(sql).df()
-- ============================================================================

-- ============================================================================
-- CTE 1: Route Sequence & Geospatial Deltas
-- Purpose: Order waypoints and calculate movement vectors between stops
-- Window Functions: LAG() for previous stop coordinates and timestamps
-- ============================================================================
WITH RouteSequence AS (
    SELECT 
        waypoint_id,
        stop_name,
        region,
        latitude,
        longitude,
        timestamp,
        wind_speed_ms,
        wind_gust_ms,
        temperature_c,
        precipitation_mm,
        road_status,
        fuel_price_isk,
        fuel_brand,
        campsite_fee_isk,
        campsite_availability,
        daylight_hours,
        
        -- Previous stop coordinates for distance calculation
        LAG(latitude, 1) OVER (ORDER BY waypoint_id) AS prev_lat,
        LAG(longitude, 1) OVER (ORDER BY waypoint_id) AS prev_lon,
        LAG(timestamp, 1) OVER (ORDER BY waypoint_id) AS prev_timestamp,
        LAG(wind_gust_ms, 1) OVER (ORDER BY waypoint_id) AS prev_wind_gust,
        LAG(fuel_price_isk, 1) OVER (ORDER BY waypoint_id) AS prev_fuel_price,
        
        -- Next stop for forward-looking analysis
        LEAD(latitude, 1) OVER (ORDER BY waypoint_id) AS next_lat,
        LEAD(longitude, 1) OVER (ORDER BY waypoint_id) AS next_lon,
        LEAD(fuel_price_isk, 1) OVER (ORDER BY waypoint_id) AS next_fuel_price,
        LEAD(wind_gust_ms, 1) OVER (ORDER BY waypoint_id) AS next_wind_gust,
        LEAD(campsite_availability, 1) OVER (ORDER BY waypoint_id) AS next_campsite_status,
        LEAD(waypoint_id, 1) OVER (ORDER BY waypoint_id) AS next_waypoint_id
    FROM read_parquet('data/iceland_raw_telemetry.parquet')
),

-- ============================================================================
-- CTE 2: Segment Metrics & Distance Calculations
-- Purpose: Calculate leg distances, elevation approximations, and travel times
-- Uses Haversine formula for geospatial distance between waypoints
-- ============================================================================
RouteMetrics AS (
    SELECT 
        *,
        
        -- Haversine distance calculation (km)
        -- Earth radius ~6371 km, using degrees to radians conversion
        ROUND(
            6371.0 * 2 * ASIN(
                SQRT(
                    POWER(SIN(RADIANS(latitude - prev_lat) / 2.0), 2) +
                    COS(RADIANS(latitude)) * COS(RADIANS(prev_lat)) *
                    POWER(SIN(RADIANS(longitude - prev_lon) / 2.0), 2)
                )
            ), 2
        ) AS segment_distance_km,
        
        -- Time delta between stops (hours)
        CASE 
            WHEN prev_timestamp IS NOT NULL 
            THEN ROUND(
                (EXTRACT(EPOCH FROM timestamp::TIMESTAMP) - 
                 EXTRACT(EPOCH FROM prev_timestamp::TIMESTAMP)) / 3600.0, 2
            )
            ELSE NULL 
        END AS segment_hours,
        
        -- Wind gust delta (change from previous segment)
        CASE 
            WHEN prev_wind_gust IS NOT NULL 
            THEN ROUND(wind_gust_ms - prev_wind_gust, 1)
            ELSE NULL 
        END AS wind_gust_delta,
        
        -- Fuel price delta from previous station
        CASE 
            WHEN prev_fuel_price IS NOT NULL 
            THEN ROUND(fuel_price_isk - prev_fuel_price, 1)
            ELSE NULL 
        END AS fuel_price_delta_from_prev
    FROM RouteSequence
),

-- ============================================================================
-- CTE 3: Rolling Weather Risk Engine
-- Purpose: Smooth wind volatility using rolling windows and create risk tiers
-- Window Functions: AVG() OVER with ROWS BETWEEN, NTILE() for quantiles
-- ============================================================================
RollingRiskEngine AS (
    SELECT 
        waypoint_id,
        stop_name,
        region,
        latitude,
        longitude,
        segment_distance_km,
        segment_hours,
        wind_speed_ms,
        wind_gust_ms,
        wind_gust_delta,
        temperature_c,
        precipitation_mm,
        road_status,
        fuel_price_isk,
        fuel_brand,
        fuel_price_delta_from_prev,
        next_fuel_price,
        next_wind_gust,
        next_campsite_status,
        campsite_fee_isk,
        campsite_availability,
        daylight_hours,
        
        -- Rolling 3-stop average wind gust (1 preceding, current, 1 following)
        ROUND(
            AVG(wind_gust_ms) OVER (
                ORDER BY waypoint_id 
                ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING
            ), 1
        ) AS rolling_3stop_gust_avg,
        
        -- Rolling 3-stop max wind gust for peak risk detection
        MAX(wind_gust_ms) OVER (
            ORDER BY waypoint_id 
            ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING
        ) AS rolling_3stop_gust_max,
        
        -- Rolling 3-stop average wind speed
        ROUND(
            AVG(wind_speed_ms) OVER (
                ORDER BY waypoint_id 
                ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING
            ), 1
        ) AS rolling_3stop_wind_avg,
        
        -- Regional rolling average (partition by region)
        ROUND(
            AVG(wind_gust_ms) OVER (
                PARTITION BY region 
                ORDER BY waypoint_id 
                ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING
            ), 1
        ) AS regional_rolling_gust_avg,
        
        -- Hazard classification based on camper van safety thresholds
        CASE 
            WHEN road_status = 'Impassable' THEN 'CRITICAL: Road Closed'
            WHEN wind_gust_ms >= 18 THEN 'CRITICAL: Rollover Risk >18 m/s'
            WHEN wind_gust_ms >= 15 THEN 'HIGH: Strong Crosswinds 15-17 m/s'
            WHEN wind_gust_ms >= 12 THEN 'MODERATE: Caution 12-14 m/s'
            WHEN wind_gust_ms >= 8 THEN 'LOW: Moderate Winds 8-11 m/s'
            ELSE 'CLEAR: Safe Driving <8 m/s'
        END AS hazard_level,
        
        -- Numeric hazard score for sorting/quantiles
        CASE 
            WHEN road_status = 'Impassable' THEN 5
            WHEN wind_gust_ms >= 18 THEN 4
            WHEN wind_gust_ms >= 15 THEN 3
            WHEN wind_gust_ms >= 12 THEN 2
            WHEN wind_gust_ms >= 8 THEN 1
            ELSE 0
        END AS hazard_score,
        
        -- Partition route into 4 risk quartiles using NTILE
        NTILE(4) OVER (ORDER BY wind_gust_ms DESC) AS wind_risk_quartile,
        
        -- Also partition by region for regional risk comparison
        NTILE(3) OVER (PARTITION BY region ORDER BY wind_gust_ms DESC) AS regional_risk_tier
        
    FROM RouteMetrics
),

-- ============================================================================
-- CTE 4: Cumulative Economics & Fuel Arbitrage Engine
-- Purpose: Running totals for distance, fuel, cost and forward-looking pricing
-- Window Functions: SUM() OVER for cumulative, LEAD() for next-station pricing
-- ============================================================================
EconomicsEngine AS (
    SELECT 
        *,
        
        -- Cumulative distance along Ring Road
        ROUND(
            SUM(COALESCE(segment_distance_km, 0)) OVER (
                ORDER BY waypoint_id 
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ), 2
        ) AS cumulative_km_driven,
        
        -- Estimated fuel consumption per segment
        -- Base: 11L/100km, adjusted for wind resistance (+0.2L per m/s wind)
        ROUND(
            COALESCE(segment_distance_km, 0) * (0.11 + (wind_speed_ms * 0.002)), 2
        ) AS est_fuel_consumed_liters,
        
        -- Cumulative fuel consumption
        ROUND(
            SUM(COALESCE(segment_distance_km, 0) * (0.11 + (wind_speed_ms * 0.002))) OVER (
                ORDER BY waypoint_id 
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ), 2
        ) AS cumulative_fuel_liters,
        
        -- Segment fuel cost
        ROUND(
            COALESCE(segment_distance_km, 0) * (0.11 + (wind_speed_ms * 0.002)) * fuel_price_isk, 0
        ) AS segment_fuel_cost_isk,
        
        -- Cumulative fuel cost
        ROUND(
            SUM(COALESCE(segment_distance_km, 0) * (0.11 + (wind_speed_ms * 0.002)) * fuel_price_isk) OVER (
                ORDER BY waypoint_id 
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ), 0
        ) AS cumulative_fuel_cost_isk,
        
        -- Next station fuel price (forward-looking)
        next_fuel_price,
        
        -- Fuel price arbitrage: current vs next station
        ROUND(fuel_price_isk - COALESCE(next_fuel_price, fuel_price_isk), 1) AS fuel_price_delta_vs_next,
        
        -- Fuel strategy recommendation
        CASE 
            WHEN fuel_price_isk - COALESCE(next_fuel_price, fuel_price_isk) < -10 
            THEN '🟢 FILL UP HERE: Cheaper by ' || ROUND(COALESCE(next_fuel_price, fuel_price_isk) - fuel_price_isk, 1) || ' ISK/L'
            WHEN fuel_price_isk - COALESCE(next_fuel_price, fuel_price_isk) > 10 
            THEN '🔴 SKIP: Next station cheaper by ' || ROUND(fuel_price_isk - COALESCE(next_fuel_price, fuel_price_isk), 1) || ' ISK/L'
            ELSE '🟡 NEUTRAL: Similar pricing'
        END AS fuel_strategy_action,
        
        -- Campsite strategy
        CASE 
            WHEN campsite_availability = 'Full' AND next_campsite_status = 'Available'
            THEN '⚠️ CURRENT FULL: Next site available - consider pushing on'
            WHEN campsite_availability = 'Available' AND next_campsite_status = 'Full'
            THEN '✅ STAY HERE: Next site is full'
            WHEN campsite_availability = 'Limited'
            THEN '⚠️ LIMITED: Book soon or have backup'
            ELSE '🟢 NORMAL: Standard availability'
        END AS campsite_strategy,
        
        -- Daily cost estimate (fuel + campsite)
        ROUND(
            (COALESCE(segment_distance_km, 0) * (0.11 + (wind_speed_ms * 0.002)) * fuel_price_isk) + campsite_fee_isk, 0
        ) AS daily_total_cost_isk
        
    FROM RollingRiskEngine
),

-- ============================================================================
-- CTE 5: Day-Level Aggregation & Trip Summary
-- Purpose: Aggregate to daily level for trip planning dashboard
-- ============================================================================
DailyTripSummary AS (
    SELECT 
        waypoint_id,
        stop_name,
        region,
        segment_distance_km,
        cumulative_km_driven,
        hazard_level,
        hazard_score,
        wind_risk_quartile,
        regional_risk_tier,
        rolling_3stop_gust_avg,
        rolling_3stop_gust_max,
        fuel_strategy_action,
        campsite_strategy,
        daily_total_cost_isk,
        cumulative_fuel_cost_isk,
        cumulative_fuel_liters,
        fuel_price_isk,
        campsite_fee_isk,
        campsite_availability,
        daylight_hours,
        road_status,
        wind_gust_ms,
        wind_speed_ms,
        
        -- Day number (assuming ~2 waypoints per day for 10-day trip)
        CEIL(waypoint_id / 2.0) AS day_number,
        
        -- Cumulative cost by day
        SUM(daily_total_cost_isk) OVER (
            PARTITION BY CEIL(waypoint_id / 2.0)
            ORDER BY waypoint_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_daily_cost_isk
        
    FROM EconomicsEngine
)

-- ============================================================================
-- FINAL OUTPUT VIEW
-- Select all enriched columns for dashboard consumption
-- ============================================================================
SELECT 
    waypoint_id,
    day_number,
    stop_name,
    region,
    latitude,
    longitude,
    segment_distance_km,
    cumulative_km_driven,
    segment_hours,
    road_status,
    wind_speed_ms,
    wind_gust_ms,
    wind_gust_delta,
    rolling_3stop_gust_avg,
    rolling_3stop_gust_max,
    regional_rolling_gust_avg,
    hazard_level,
    hazard_score,
    wind_risk_quartile,
    regional_risk_tier,
    temperature_c,
    precipitation_mm,
    daylight_hours,
    fuel_price_isk,
    fuel_brand,
    next_fuel_price,
    fuel_price_delta_vs_next,
    fuel_strategy_action,
    est_fuel_consumed_liters,
    segment_fuel_cost_isk,
    cumulative_fuel_liters,
    cumulative_fuel_cost_isk,
    campsite_fee_isk,
    campsite_availability,
    campsite_strategy,
    daily_total_cost_isk,
    cumulative_daily_cost_isk
FROM DailyTripSummary
ORDER BY waypoint_id;