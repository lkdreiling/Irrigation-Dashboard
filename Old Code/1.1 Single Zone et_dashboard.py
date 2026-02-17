import streamlit as st
import pandas as pd
import requests

# 1. Page Configuration
st.set_page_config(page_title="Irrigation Master", layout="wide", page_icon="🌱")
st.title("🌱 Precision Irrigation & Soil Dashboard")

# 2. Robust Zip Code Lookup
def get_coords_from_zip(zip_code):
    if not zip_code or len(zip_code) != 5:
        return None, None, None
    try:
        url = f"http://api.zippopotam.us/us/{zip_code}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            lat = float(data['places'][0]['latitude'])
            lon = float(data['places'][0]['longitude'])
            name = f"{data['places'][0]['place name']}, {data['places'][0]['state abbreviation']}"
            return lat, lon, name
    except:
        return None, None, None
    return None, None, None

# 3. SIDEBAR: Settings
st.sidebar.header("📍 Location")
zip_input = st.sidebar.text_input("Enter Zip Code", value="48892")
z_lat, z_lon, z_name = get_coords_from_zip(zip_input)

# Fallback for Westphalia, MI
final_lat = float(z_lat) if z_lat else 42.9286
final_lon = float(z_lon) if z_lon else -84.7981
location_display = z_name if z_name else "Westphalia, MI"

lat = st.sidebar.number_input("Latitude", value=final_lat, format="%.4f")
lon = st.sidebar.number_input("Longitude", value=final_lon, format="%.4f")
st.sidebar.write(f"Current Area: **{location_display}**")

st.sidebar.divider()

# 4. SIDEBAR: Updated Labels
st.sidebar.header("💧 Irrigation Requirements")
area_sqft = st.sidebar.number_input("Zone Area (Square Feet)", value=1000)
flow_rate = st.sidebar.number_input("Zone Flow (GPM)", value=5.0)
soil_type = st.sidebar.selectbox("Soil Type", ["Sand", "Loamy Sand", "Sandy Loam", "Loam", "Silt Loam", "Clay Loam", "Clay"], index=3)

soil_awc = {"Sand": 0.06, "Loamy Sand": 0.09, "Sandy Loam": 0.13, "Loam": 0.18, "Silt Loam": 0.22, "Clay Loam": 0.20, "Clay": 0.17}
root_depth = st.sidebar.slider("Root Depth (Inches)", 4, 36, 12)
mad = st.sidebar.slider("Allowable Depletion (%)", 10, 60, 50)

# 5. Math Calculations
total_storage = soil_awc[soil_type] * root_depth
safe_storage = total_storage * (mad / 100)

# 6. Fetch Weather Data
@st.cache_data(ttl=3600)
def fetch_weather(l, n):
    api_url = f"https://api.open-meteo.com/v1/forecast?latitude={l}&longitude={n}&hourly=et0_fao_evapotranspiration&timezone=auto&forecast_days=7"
    try:
        r = requests.get(api_url, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None

# 7. Logic and Display
weather_data = fetch_weather(lat, lon)

if isinstance(weather_data, dict) and "hourly" in weather_data:
    df_h = pd.DataFrame({
        "time": pd.to_datetime(weather_data["hourly"]["time"]),
        "et0": [v / 25.4 for v in weather_data["hourly"]["et0_fao_evapotranspiration"]]
    })
    df_d = df_h.set_index("time").resample('D').sum().reset_index()
    
    today_et = df_d.iloc[0]['et0']
    gallons = today_et * area_sqft * 0.623
    runtime = gallons / flow_rate if flow_rate > 0 else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Today's Water Loss", f"{today_et:.3f} in")
    m2.metric("Water to Replace", f"{gallons:.1f} Gal")
    
    days_rem = safe_storage / today_et if today_et > 0 else 0
    m3.metric("Days of Water Left", f"{days_rem:.1f}")

    st.success(f"⏱️ **Irrigation Timer:** Run **Zone** for **{runtime:.1f} minutes** today.")
    st.divider()
    
    c1, c2 = st.columns([2, 1])
    with c1:
        st.subheader("7-Day Forecast")
        st.line_chart(df_h.set_index("time"))
    with c2:
        st.subheader("Daily Totals")
        st.dataframe(df_d.style.format({"et0": "{:.3f}"}), hide_index=True)

    # 8. MINIMIZABLE TAB 1: Calculation Logic
    with st.expander("📊 View Detailed Calculation Logic (Audit Trail)"):
        st.write(f"### Soil & Storage Breakdown for {soil_type}")
        audit_data = {
            "Step": ["1. Unit Holding (AWC)", "2. Root Depth", "3. Total Capacity", "4. Allowed Depletion", "5. Manageable Pool"],
            "Math / Formula": ["Raw Soil Value", "User Input", "AWC × Depth", "User Input", "Total Cap × MAD %"],
            "Result": [f"{soil_awc[soil_type]} in/in", f"{root_depth} in", f"{total_storage:.2f} in", f"{mad}%", f"{safe_storage:.2f} in"]
        }
        st.table(pd.DataFrame(audit_data))
        st.divider()
        st.write("### Today's Irrigation Volume & Runtime")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"""
            **1. Volume Calculation**
            * **Formula:** Area × ET × 0.623
            * **Your Math:** {area_sqft} sq ft × {today_et:.3f}" × 0.623
            * **Result:** **{gallons:.1f} Gallons Needed**
            """)
        with col_b:
            st.markdown(f"""
            **2. Runtime Calculation**
            * **Formula:** Gallons Needed / GPM
            * **Your Math:** {gallons:.1f} gal / {flow_rate} GPM
            * **Result:** **{runtime:.1f} Minutes Runtime**
            """)

    # 9. MINIMIZABLE TAB 2: Weather Science
    with st.expander("🔬 Weather Science & FAO-56 Methodology"):
        st.write("### How is 'Water Loss' Calculated?")
        st.markdown("""
        The "Today's Water Loss" value is technically known as **ET₀** (Reference Evapotranspiration). 
        This dashboard utilizes the **FAO-56 Penman-Monteith equation**, which is the global 
        standard for irrigation management.
        
        **The equation accounts for four primary environmental variables:**
        1. **Solar Radiation:** The main energy source that evaporates water from leaf surfaces.
        2. **Air Temperature:** Warm air holds more water vapor, increasing the "pull" on the plant.
        3. **Relative Humidity:** Drier air accelerates evaporation; humid air slows it down.
        4. **Wind Speed:** Wind removes the "boundary layer" of saturated air around the leaf, speeding up water loss.
        """)
        
        st.info("""
        **Data Source:** This app connects to the Open-Meteo API, which aggregates data from global 
        high-resolution weather models (like the HRRR and GFS) to calculate ET₀ for your 
        exact Latitude and Longitude.
        """)
        
        st.write("---")
        st.caption("Reference: Allen, R. G., Pereira, L. S., Raes, D., & Smith, M. (1998). Crop evapotranspiration-Guidelines for computing crop water requirements-FAO Irrigation and drainage paper 56.")

else:
    st.error("Weather Data Error. Please check your connection or Zip Code.")
