import requests

# This dictionary is what et_dashboard.py is looking for
#### 3. SOIL PHYSICS DATABASE (Rosetta Class Averages)
    # Rosetta theta_s (cm3/cm3) converted to in/ft (Value * 12)
    # PWP values are based on standard agricultural averages for these textures
SOIL_DATA = {
    "Sand":       {"FC": 0.375 * 12, "PWP": 0.60}, 
    "Loamy Sand": {"FC": 0.390 * 12, "PWP": 0.95},
    "Sandy Loam": {"FC": 0.387 * 12, "PWP": 1.30},
    "Loam":       {"FC": 0.399 * 12, "PWP": 1.70},
    "Silt Loam":  {"FC": 0.439 * 12, "PWP": 1.90},
    "Silt":       {"FC": 0.489 * 12, "PWP": 1.50},
    "Sandy Clay Loam": {"FC": 0.384 * 12, "PWP": 2.10},
    "Clay Loam":  {"FC": 0.442 * 12, "PWP": 2.40},
    "Silty Clay Loam": {"FC": 0.482 * 12, "PWP": 2.50},
    "Sandy Clay": {"FC": 0.385 * 12, "PWP": 2.60},
    "Silty Clay": {"FC": 0.479 * 12, "PWP": 2.80},
    "Clay":       {"FC": 0.459 * 12, "PWP": 3.00}
}

def get_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5).json()
        return float(res['places'][0]['latitude']), float(res['places'][0]['longitude']), f"{res['places'][0]['place name']}"
    except: 
        return 42.9286, -84.7981, "Westphalia, MI"

