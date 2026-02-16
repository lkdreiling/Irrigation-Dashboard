import streamlit as st
import pandas as pd
import requests

# 1. Page Configuration
st.set_page_config(page_title="Zip Code ET0 Tracker", layout="wide", page_icon="💧")
st.title("💧 Reference ET₀ Dashboard")

# 2. Functions for Location
@st.cache_data
def get_auto_coords():
    try:
        res = requests.get("http://ip-api.com/json/").json()
        return res['lat'], res['lon'], f"{res['city']}, {res['regionName']}"
    except:
        return 34.05, -118.24, "Default (Los Angeles)"

def get_coords_from_zip(zip_code):
    try:
        # Using Zippopotam.us for free US zip lookup
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}").json()
        if res:
            lat = float(res['places'][0]['latitude'])
            lon = float(res['places'][0]['longitude'])
            name = f"{res['places'][0]['place name']}, {res['places'][0]['state abbreviation']}"
            return lat, lon, name
    except:
        st.sidebar.error("Zip code not found. Please check and try again.")
        return None, None, None

# 3. SIDEBAR: Location Inputs
st.sidebar.header("📍 Location Settings")

# Zip Code Input
zip_input = st.sidebar.text_input("Enter US Zip Code (Optional)", help="Enter a 5-digit zip code and press Enter")

# Initialize default coordinates
auto_lat, auto_lon, auto_name = get_auto_coords()

# Logic to override coordinates if Zip is entered
if zip_input:
    z_lat, z_lon, z_name = get_coords_from_zip(zip_input)
    if z_lat:
        auto_lat, auto_lon, auto_name = z_lat, z_lon, z_name

# Display and allow manual coordinate overrides
st.sidebar.write(f"Current Area: **{auto_name}**")
lat = st.sidebar.number_input("Latitude", value=auto_lat, format="%.4f")
lon = st.sidebar.number_input("Longitude", value=auto_lon, format="%.4f")

# 4. Fetch Data and Convert to Inches
@st.cache_data(ttl=3600)
def get_et_data(latitude, longitude):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "et0_fao_evapotranspiration",
        "timezone": "auto",
        "forecast_days": 7
    }
    response = requests.get(url, params=params)
    data = response.json()
    
    # Create DataFrame and convert mm to inches
    df = pd.DataFrame({
        "time": pd.to_datetime(data["hourly"]["time"]),
        "et0_in": [val / 25.4 for val in data["hourly"]["et0_fao_evapotranspiration"]]
    })
    return df

# 5. Dashboard Logic
try:
    df_hourly = get_et_data(lat, lon)
    df_daily = df_hourly.set_index("time").resample('D').sum().reset_index()

    # Today's Highlight Metric
    today_val = df_daily.iloc[0]['et0_in']
    st.metric("Total Reference ET Today", f"{today_val:.3f} in")

    # Layout Columns
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Hourly ET₀ Forecast")
        st.line_chart(df_hourly.set_index("time"))

    with col2:
        st.subheader("Daily Totals Table")
        st.dataframe(df_daily.style.format({"et0_in": "{:.3f}"}), hide_index=True)
        
    st.subheader("Weekly Trend")
    st.bar_chart(df_daily.set_index("time"))

except Exception as e:
    st.error(f"Error: {e}")

# 6. CSV Download
csv = df_daily.to_csv(index=False).encode('utf-8')
st.download_button("Download Data (Inches)", csv, "et_data_inches.csv", "text/csv")
