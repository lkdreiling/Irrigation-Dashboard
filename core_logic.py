import requests

# This dictionary is what et_dashboard.py is looking for
#### 1. SOIL PHYSICS DATABASE 
# Values from Saxton & Rawls (2006). 
# FC and PWP are volumetric fractions (m3/m3)
SOIL_DATA = {
    "Sand":            {"FC": 0.07, "PWP": 0.03},
    "Loamy Sand":      {"FC": 0.11, "PWP": 0.05},
    "Sandy Loam":      {"FC": 0.18, "PWP": 0.08},
    "Loam":            {"FC": 0.25, "PWP": 0.12},
    "Silt Loam":       {"FC": 0.29, "PWP": 0.13}, # <-- Fixed: Drastically narrows the gap
    "Silt":            {"FC": 0.30, "PWP": 0.12}, # <-- Fixed: Prevents impossible 0.24 gap
    "Sandy Clay Loam": {"FC": 0.27, "PWP": 0.17},
    "Clay Loam":       {"FC": 0.32, "PWP": 0.20},
    "Silty Clay Loam": {"FC": 0.36, "PWP": 0.21},
    "Sandy Clay":      {"FC": 0.34, "PWP": 0.24},
    "Silty Clay":      {"FC": 0.38, "PWP": 0.26},
    "Clay":            {"FC": 0.40, "PWP": 0.28}
}

#### 1b. PLANT / CROP COEFFICIENT DATABASE
# Kc values approximate mid-season crop coefficients relative to FAO-56 reference ET0.
# Sources: Allen et al. 1998 (FAO-56 Table 12) for turf/tree/vegetable ranges; Costello & Jones,
# 'WUCOLS IV' (2014) landscape-coefficient "plant factor" categories for ornamental groupings.
PLANT_DATA = {
    "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)": {"Kc": 0.80, "DefaultDepth": 6},
    "Warm-Season Turf (Bermuda, Zoysia, St. Augustine)": {"Kc": 0.60, "DefaultDepth": 6},
    "Shrub / Landscape Bed": {"Kc": 0.50, "DefaultDepth": 12},
    "Groundcover": {"Kc": 0.60, "DefaultDepth": 8},
    "Trees (Established)": {"Kc": 0.55, "DefaultDepth": 18},
    "Annual / Flower Bed": {"Kc": 0.80, "DefaultDepth": 6},
    "Xeriscape / Native / Drought-Tolerant": {"Kc": 0.30, "DefaultDepth": 12},
}

#### 1c. IRRIGATION HEAD TYPE AVERAGES (Easy Mode area/flow estimation)
# Derived from irrigation_head_specs/head_database.csv (Rain Bird/Hunter/Toro/Orbit core
# residential lineup, compiled 2026-07-18) -- see that folder's SOURCES.md for the underlying
# manufacturer datasheets. Spray/Rotor values are the average GPM and radius across each
# brand's catalog nozzle sizes. Drip uses a flat GPH-per-sqft assumption (~0.6 GPH emitter on
# a ~12" grid) instead, since drip zones aren't sized by head-to-head spacing the way spray/
# rotor zones are.
HEAD_TYPE_DATA = {
    "Spray": {"avg_gpm_per_head": 2.3, "avg_radius_ft": 10.5},
    "Rotor": {"avg_gpm_per_head": 3.9, "avg_radius_ft": 37.0},
    "Drip": {"gph_per_sqft": 0.6},
}

# Typical mature coverage footprint per plant, by size bucket -- used to turn a plant count
# into an area estimate for Drip zones, since individual emitters/dripline aren't something a
# homeowner can count the way sprinkler heads are. Roughly square spacing (Small ~3ft,
# Medium ~5ft, Large ~8ft) consistent with common landscape planting-spacing guidance.
PLANT_SIZE_AREA_SQFT = {
    "Small (annuals, small perennials)": 9,
    "Medium (typical shrub)": 25,
    "Large (small tree, big shrub)": 64,
}

def estimate_area_and_flow(head_type, unit_count, plant_size="Medium (typical shrub)"):
    """
    Easy Mode helper: estimates zone area (sq ft) and flow (GPM) from a quantity a homeowner
    can actually count, instead of asking them to know square footage -- head count for
    Spray/Rotor (spacing = radius, the same 50% diameter convention manufacturers use to
    publish matched-precipitation rates), or plant count for Drip (via PLANT_SIZE_AREA_SQFT,
    since drip emitters/dripline aren't a countable unit the way heads are).
    Returns (estimated_area_sqft, estimated_flow_gpm, avg_radius_ft, area_per_plant_sqft) --
    avg_radius_ft is None for Drip and area_per_plant_sqft is None for Spray/Rotor, since
    only one model applies to a given head type.
    """
    if head_type == "Drip":
        area_per_plant = PLANT_SIZE_AREA_SQFT.get(plant_size, PLANT_SIZE_AREA_SQFT["Medium (typical shrub)"])
        area_sqft = unit_count * area_per_plant
        flow_gpm = (area_sqft * HEAD_TYPE_DATA["Drip"]["gph_per_sqft"]) / 60.0
        return round(area_sqft), round(flow_gpm, 2), None, area_per_plant

    data = HEAD_TYPE_DATA.get(head_type, HEAD_TYPE_DATA["Spray"])
    radius = data["avg_radius_ft"]
    area_sqft = unit_count * (radius ** 2)
    flow_gpm = unit_count * data["avg_gpm_per_head"]
    return round(area_sqft), round(flow_gpm, 2), radius, None

#### 2. Coordinate Lookup
def get_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5).json()
        lat = float(res['places'][0]['latitude'])
        lon = float(res['places'][0]['longitude'])
        name = f"{res['places'][0]['place name']}, {res['places'][0]['state abbreviation']}"
        return lat, lon, name
    except:
        return 42.9286, -84.7981, "Westphalia, MI"

#### 3. Irrigation Logic 
def calculate_irrigation_limits(soil_type, root_depth_inches, mad_percent):
    """
    Calculates the irrigation thresholds based on soil physics.
    Returns: (AW_per_foot, PAW_total, AD_limit)
    """
    # Use .get() to avoid errors if a soil type is missing, defaulting to Loam
    soil = SOIL_DATA.get(soil_type, SOIL_DATA["Loam"])
    
    # 1. Available Water (inches per foot)
    # (FC - PWP) * 12 inches
    aw_per_foot = (soil["FC"] - soil["PWP"]) * 12
    
    # 2. Plant Available Water (total inches in root zone)
    # AW * (Root Depth / 12)
    paw_total = aw_per_foot * (root_depth_inches / 12)
    
    # 3. Allowable Depletion (the 'Water Now' trigger)
    # PAW * (MAD / 100)
    ad_limit = paw_total * (mad_percent / 100)
    
    return round(aw_per_foot, 3), round(paw_total, 3), round(ad_limit, 3)
