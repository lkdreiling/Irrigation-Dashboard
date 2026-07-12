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
