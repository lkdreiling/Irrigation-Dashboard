import requests

# This dictionary is what et_dashboard.py is looking for
#### 3. SOIL PHYSICS DATABASE 
# Values from Saxton & Rawls (2006). 
# FC and PWP are volumetric fractions (m3/m3)
# DrainDays is the time (in days) to return from Saturation to Field Capacity
# SOIL_DATA with Van Genuchten Parameters (Rosetta Class Averages)
# alpha [1/cm], n [dimensionless], theta_r [residual], theta_s [saturated]
SOIL_DATA = {
    "Sand":            {"FC": 0.10, "PWP": 0.05, "DrainDays": 1.2, "alpha": 0.145, "n": 2.68, "theta_r": 0.045, "theta_s": 0.43},
    "Loamy Sand":      {"FC": 0.12, "PWP": 0.05, "DrainDays": 1.5, "alpha": 0.124, "n": 2.28, "theta_r": 0.057, "theta_s": 0.41},
    "Sandy Loam":      {"FC": 0.18, "PWP": 0.08, "DrainDays": 2.0, "alpha": 0.075, "n": 1.89, "theta_r": 0.065, "theta_s": 0.41},
    "Loam":            {"FC": 0.28, "PWP": 0.14, "DrainDays": 2.5, "alpha": 0.036, "n": 1.56, "theta_r": 0.078, "theta_s": 0.43},
    "Silt Loam":       {"FC": 0.31, "PWP": 0.11, "DrainDays": 3.0, "alpha": 0.020, "n": 1.41, "theta_r": 0.067, "theta_s": 0.45},
    "Silt":            {"FC": 0.30, "PWP": 0.06, "DrainDays": 3.0, "alpha": 0.016, "n": 1.37, "theta_r": 0.034, "theta_s": 0.46},
    "Sandy Clay Loam": {"FC": 0.27, "PWP": 0.17, "DrainDays": 3.5, "alpha": 0.059, "n": 1.48, "theta_r": 0.100, "theta_s": 0.39},
    "Clay Loam":       {"FC": 0.36, "PWP": 0.22, "DrainDays": 4.5, "alpha": 0.019, "n": 1.31, "theta_r": 0.095, "theta_s": 0.41},
    "Silty Clay Loam": {"FC": 0.38, "PWP": 0.22, "DrainDays": 5.0, "alpha": 0.010, "n": 1.23, "theta_r": 0.089, "theta_s": 0.43},
    "Sandy Clay":      {"FC": 0.34, "PWP": 0.25, "DrainDays": 6.0, "alpha": 0.027, "n": 1.23, "theta_r": 0.100, "theta_s": 0.38},
    "Silty Clay":      {"FC": 0.41, "PWP": 0.27, "DrainDays": 7.0, "alpha": 0.005, "n": 1.09, "theta_r": 0.070, "theta_s": 0.36},
    "Clay":            {"FC": 0.42, "PWP": 0.30, "DrainDays": 8.0, "alpha": 0.008, "n": 1.09, "theta_r": 0.068, "theta_s": 0.38}
}

# 2. Coordinate Lookup
def get_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5).json()
        lat = float(res['places'][0]['latitude'])
        lon = float(res['places'][0]['longitude'])
        name = f"{res['places'][0]['place name']}, {res['places'][0]['state abbreviation']}"
        return lat, lon, name
    except:
        return 42.9286, -84.7981, "Westphalia, MI"

# 3. Irrigation Logic 
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
