import streamlit as st
import pandas as pd
import requests
import json
import os
import shutil
import altair as alt
from datetime import datetime
import openmeteo_requests
import requests_cache
from retry_requests import retry

#### 1. Page Configuration
st.set_page_config(page_title="Irrigation Dashboard", layout="wide", page_icon="🌱")
st.markdown("""
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 0rem;
            margin-top: 0rem;
        }
    </style>
""", unsafe_allow_html=True)
st.title("🌱 Irrigation Dashboard")

from data_manager import (
    load_json, save_json, get_prop_paths, 
    PROP_LIST_FILE, DATA_DIR, SYSTEM_DIR, BACKUP_DIR,  # <--- Make sure SYSTEM_DIR is here
    save_properties_master  
)
from core_logic import SOIL_DATA, get_coords, calculate_irrigation_limits


# Initialize Session State
if 'prop_master' not in st.session_state:
    st.session_state.prop_master = load_json(PROP_LIST_FILE, {"Home": "48892"})

# --- TOP NAVIGATION: Dropdown & Settings ---
head_col1, head_col2 = st.columns([3, 1])

with head_col1:
    # Pull the list of names from our dictionary keys
    prop_names = list(st.session_state.prop_master.keys())
    active_prop = st.selectbox("Select Property", prop_names, label_visibility="collapsed")
    # Get the zip associated with that property name
    active_zip = st.session_state.prop_master.get(active_prop, "48892")

with head_col2:
    with st.popover("⚙️ Property Settings"):
        st.subheader("Add New Property")
        new_p_name = st.text_input("Property Name", key="new_name")
        new_p_zip = st.text_input("Property Zip Code", key="new_zip")
        if st.button("➕ Create"):
            if new_p_name and new_p_zip:
                st.session_state.prop_master[new_p_name] = new_p_zip
                save_properties_master(st.session_state.prop_master)
                st.rerun()
        
        st.divider()
        st.subheader("Update Current Zip")
        current_zip_edit = st.text_input(f"Edit Zip for {active_prop}", value=active_zip)
        if st.button("💾 Update Zip"):
            st.session_state.prop_master[active_prop] = current_zip_edit
            save_properties_master(st.session_state.prop_master)
            st.rerun()
        
        st.divider()
        st.subheader("Danger Zone")
        if st.button(f"💥 Wipe {active_prop} Zones", type="primary"):
            for suffix in ["_profiles.json", "_log.json"]:
                path = os.path.join(DATA_DIR, f"{active_prop}{suffix}")
                if os.path.exists(path): 
                    os.remove(path)
            st.warning(f"Zones for {active_prop} deleted.")
            st.rerun()
                             


# --- FILE PATHS ---
# Zones and Logs stay in main folder for easy access
DB_FILE = os.path.join(DATA_DIR, f"{active_prop}_profiles.json")
LOG_FILE = os.path.join(DATA_DIR, f"{active_prop}_log.json")

# WEATHER moves to the "Do Not Delete" folder
WEATHER_LOG = os.path.join(SYSTEM_DIR, f"{active_prop}_weather.json")

def load_profiles():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: pass
    return {"Default Zone": {"zip": "48892", "area": 1000, "flow": 5.0, "soil": "Loam", "depth": 12, "mad": 50}}

def save_profiles(p):
    with open(DB_FILE, "w") as f: json.dump(p, f)

def load_logs():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f: return json.load(f)
        except: pass
    return {}

def save_log(zone, minutes, inches_applied):
    logs = load_logs()
    if zone not in logs: logs[zone] = []
    logs[zone].append({"date": str(datetime.now().date()), "minutes": minutes, "inches": inches_applied})
    with open(LOG_FILE, "w") as f: json.dump(logs, f)

def archive_weather(df_daily):
    history = {}
    if os.path.exists(WEATHER_LOG):
        with open(WEATHER_LOG, "r") as f: history = json.load(f)
    for _, row in df_daily.iterrows():
        date_str = row['time'].strftime('%Y-%m-%d')
        if row['time'].date() <= datetime.now().date():
            history[date_str] = {"ET0 (in)": row["ET0 (in)"], "Rain (in)": row["Rain (in)"]}
    with open(WEATHER_LOG, "w") as f: json.dump(history, f)

def load_weather_history():
    if os.path.exists(WEATHER_LOG):
        with open(WEATHER_LOG, "r") as f:
            data = json.load(f)
            df = pd.DataFrame.from_dict(data, orient='index').reset_index()
            df.columns = ['time', 'ET0 (in)', 'Rain (in)']
            df['time'] = pd.to_datetime(df['time'])
            return df
    return pd.DataFrame(columns=['time', 'ET0 (in)', 'Rain (in)'])

profiles = load_profiles()






#### 4. SIDEBAR
st.sidebar.header(f"📍 {active_prop} Zones")
zone_list = list(profiles.keys())
selected_zone_name = st.sidebar.selectbox("Select Active Zone", zone_list)
current_zone = profiles.get(selected_zone_name, profiles[zone_list[0]])

# Input for creating a new zone name
new_zone_name = st.sidebar.text_input("New Zone Name (Optional)", placeholder="e.g. Backyard")

@st.cache_data(ttl=3600)
def get_coords(zip_code):
    try:
        # Uses the active_zip from the Property Settings at the top
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5).json()
        return float(res['places'][0]['latitude']), float(res['places'][0]['longitude']), f"{res['places'][0]['place name']}"
    except: 
        return 42.9286, -84.7981, "Westphalia, MI"

lat, lon, z_name = get_coords(active_zip)

st.sidebar.header("💧 Irrigation Specs")
area = st.sidebar.number_input("Zone Area (sq ft)", value=float(current_zone.get("area", 1000)))
flow = st.sidebar.number_input("Zone Flow (GPM)", value=float(current_zone.get("flow", 5.0)))
soil_types = list(SOIL_DATA.keys())
saved_soil = current_zone.get("soil", "Loam")
try:
    soil_index = soil_types.index(saved_soil)
except ValueError:
    soil_index = soil_types.index("Loam") # Fallback if name mismatch

soil_choice = st.sidebar.selectbox("Soil", soil_types, index=soil_index)
# -------------------------

depth_in = st.sidebar.slider("Root Depth (in)", 4, 36, int(current_zone.get("depth", 12)))
mad = st.sidebar.slider("MAD (%)", 10, 60, int(current_zone.get("mad", 50)))

save_col, del_col = st.sidebar.columns(2)
if save_col.button("💾 Save Zone"):
    target_name = new_zone_name if new_zone_name.strip() != "" else selected_zone_name
    profiles[target_name] = {"area": area, "flow": flow, "soil": soil_choice, "depth": depth_in, "mad": mad}
    if target_name not in profiles:
        start_dt = str((datetime.now() - pd.Timedelta(days=7)).date())
    else:
        # Keep the existing start date if we are just updating settings
        start_dt = profiles[target_name].get("start_date", str(datetime.now().date()))

    profiles[target_name] = {
        "area": area, 
        "flow": flow, 
        "soil": soil_choice, 
        "depth": depth_in, 
        "mad": mad,
        "start_date": start_dt  # <--- Logic now knows when this zone "began"
    }
    if target_name != "Default Zone" and "Default Zone" in profiles:
        del profiles["Default Zone"]
        st.toast("Placeholder 'Default Zone' removed!")
    save_profiles(profiles)
    st.rerun()

if del_col.button("🗑️ Delete Zone", type="secondary"):
    if len(profiles) > 1:
        del profiles[selected_zone_name]
        save_profiles(profiles)
        st.rerun()
    else: st.error("Cannot delete last zone.")
    
st.sidebar.divider()
st.sidebar.header("📝 Log a Watering Event")
run_mins = st.sidebar.number_input("Actual Runtime (min)", min_value=0.0, step=1.0)
if st.sidebar.button("Add to History"):
    applied_inches = (run_mins * flow) / (area * 0.623)
    save_log(selected_zone_name, run_mins, applied_inches)
    st.sidebar.success(f"Logged {applied_inches:.2f}\" applied!")
    st.rerun()



#### 5. MATH ENGINE 
# Call your new math engine
aw_per_foot, paw_total, ad_limit = calculate_irrigation_limits(soil_choice, depth_in, mad)

# 2. Grab the raw constants for the audit display (converted to inches/foot)
soil_info = SOIL_DATA.get(soil_choice, SOIL_DATA["Loam"])
fc_inft = soil_info["FC"] * 12
pwp_inft = soil_info["PWP"] * 12
rz_ft = depth_in / 12





#### 6. WEATHER ENGINE (Integrated Open-Meteo Library)

# 6.1 Setup the Open-Meteo API client with cache
cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

@st.cache_data(ttl=3600)
def fetch_weather_integrated(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ["et0_fao_evapotranspiration", "precipitation"],
        "timezone": "auto",
        "past_days": 92,
        "forecast_days": 14
    }
    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        
        # Process hourly data
        hourly = response.Hourly()
        et_values = hourly.Variables(0).ValuesAsNumpy()
        precip_values = hourly.Variables(1).ValuesAsNumpy()

        # Create the time range using the library's method
        dates = pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left"
        )

        df_hourly = pd.DataFrame({
            "time": dates,
            "et0": et_values,
            "rain": precip_values
        })
        
        # Convert UTC to Local (matches the 'timezone: auto' param)
        df_hourly['time'] = df_hourly['time'].dt.tz_convert(None) 
        
        # Resample to Daily and convert to Inches
        df_daily = df_hourly.set_index("time").resample('D').sum().reset_index()
        df_daily['ET0 (in)'] = df_daily['et0'] / 25.4
        df_daily['Rain (in)'] = df_daily['rain'] / 25.4
        
        # Critical: Strip time for comparison logic
        df_daily['time'] = df_daily['time'].dt.normalize()
        
        return df_daily[['time', 'ET0 (in)', 'Rain (in)']]
    except Exception as e:
        st.error(f"Weather API Error: {e}")
        return None

# --- Execution ---
df_api = fetch_weather_integrated(lat, lon)

if df_api is not None:
    # 1. Archive new data to permanent storage
    archive_weather(df_api)
    
    # 2. Load the full history and merge with new API data
    df_permanent = load_weather_history()
    df_daily = pd.concat([df_permanent, df_api]).drop_duplicates(subset='time', keep='last').sort_values('time')
    
    # 3. Ensure 'time' is normalized across the board
    df_daily['time'] = pd.to_datetime(df_daily['time']).dt.normalize()
    
    # 4. Filter to 92 days to keep the dataframe lean
    lookback_limit = pd.Timestamp(datetime.now().date()) - pd.Timedelta(days=92)
    df_daily = df_daily[df_daily['time'] >= lookback_limit]
    
    # 5. Merge Zone-Specific Irrigation Logs
    all_logs = load_logs()
    zone_logs = all_logs.get(selected_zone_name, [])
    
    if zone_logs:
        log_df = pd.DataFrame(zone_logs)
        log_df['time'] = pd.to_datetime(log_df['date']).dt.normalize()
        log_daily = log_df.groupby('time')['inches'].sum().reset_index()
        log_daily.columns = ['time', 'Irrigation (in)']
        df_daily = pd.merge(df_daily, log_daily, on='time', how='left').fillna(0)
    else:
        df_daily['Irrigation (in)'] = 0.0

    # 6. Split into History and Forecast for the UI
    today_dt = pd.Timestamp(datetime.now().date()).normalize()
    df_history = df_daily[df_daily['time'] < today_dt].copy()
    df_forecast = df_daily[df_daily['time'] >= today_dt].copy()




#### 7. DEFICIT CALCULATION (Isolated per Zone)
    # 7.1 Determine this zone's specific starting point
    zone_start_str = current_zone.get("start_date", str(datetime.now().date()))
    zone_start_ts = pd.Timestamp(zone_start_str).normalize()
    today_ts = pd.Timestamp(datetime.now().date()).normalize()

    # 7.2 Filter weather data to ONLY include days since this zone started
    # We include today (<= today_dt) to fix the "missing today" issue
    mask = (df_daily['time'] >= zone_start_ts) & (df_daily['time'] <= today_dt)
    zone_weather = df_daily.loc[mask]
    
    # 7.3 Sum losses and gains for this specific window
    total_et = zone_weather['ET0 (in)'].sum()
    total_rain = zone_weather['Rain (in)'].sum()
    total_irrigation = zone_weather['Irrigation (in)'].sum()
    
    total_gains = total_rain + total_irrigation
    
    # 7.4 The Resulting Deficit
    current_deficit = total_et - total_gains
    
    # Safety: Soil can't be more than "Full" (Deficit 0)
    if current_deficit < 0:
        current_deficit = 0.0

    # 7.5 Actionable Metrics
    gallons = current_deficit * area * 0.623
    runtime = gallons / flow if flow > 0 else 0
        
    
    
    
    
#### 8. Dashboard Metrics
    st.markdown(f"### {active_prop} : {selected_zone_name} <span style='color:gray; font-size:0.8em;'>({active_zip})</span>", unsafe_allow_html=True)
    st.divider()
    seven_day_et = df_forecast.iloc[0:7]['ET0 (in)'].sum()
    seven_day_rain = df_forecast.iloc[0:7]['Rain (in)'].sum()
    today_et = df_forecast.iloc[0]['ET0 (in)']
    today_rain = df_forecast.iloc[0]['Rain (in)']
    m1, m2, m3, m4 = st.columns(4)
    
    # Metric 3 is now 7-Day ET
    m3.metric("7-Day ET Forecast", f"{seven_day_et:.2f}\"", delta=f"Today: {today_et:.2f}\"", delta_color="inverse")
    
    # Metric 4 7-Day Rain
    m4.metric("7-Day Rain Forecast", f"{seven_day_rain:.2f}\"", delta=f"Today: {today_rain:.2f}\"")
    
    # Metric 1 is now the Water to Apply (Depth, Time, and Vol)
    m1.metric("Water to Apply", f"{ current_deficit:.2f}\"", 
              delta=f"{runtime:.1f} min ({gallons:.0f} gal)")
     
    # Metric 2: Allowable Depletion & Status Arrow [Logic: If deficit is less than AD, we are "OK" (Green). If deficit > AD, we are "Over" (Red).]
    # UPDATE: Changed allowable_depletion to ad_limit
    if current_deficit < ad_limit:
        status_msg = "Wait to Apply"
        d_val = "OK"
        d_color = "normal" # Shows Green
    else:
        status_msg = "Water Needed"
        d_val = "LOW"
        d_color = "inverse" # Shows Red

    m2.metric(
        label="Allowable Depletion", 
        value=f"{ad_limit:.2f}\"", 
        delta=status_msg, 
        delta_color=d_color
    )
  
    # Guidance Logic based on Forecast
    if seven_day_rain >  current_deficit and  current_deficit > 0:
        st.warning(f"🌧️ **Rain is coming:** The 7-day forecast shows **{seven_day_rain:.2f}\"** of rain. You may want to skip watering today!")
    elif  current_deficit <= 0:
        st.success(f"✅ **Soil is Hydrated!**")
    else:
        st.info(f"⏱️ **Irrigation Plan:** Run for **{runtime:.1f} minutes** to refill the profile.")
  
  
  
  
#### 9.1 Graphs & Tables
st.divider()
with st.expander("📈 View Water Balance Graph", expanded=True):
    st.write(f"### 📈 Water Balance for {selected_zone_name}")

    # 9.2 Setup Time Window (The "Secret Sauce" that fixed the blank screen)
    if not df_daily.empty:
        now_dt = pd.Timestamp(datetime.now().date()).normalize()
    # Calculation for the "Default View" (7 days back, 14 forward)
    view_start = now_dt - pd.Timedelta(days=7)
    view_end = now_dt + pd.Timedelta(days=14)
    lookback_days = 90
    data_start = now_dt - pd.Timedelta(days=lookback_days)
    df_zoom = df_daily[df_daily['time'] >= data_start].copy()
    
    # Common X-Axis with restricted initial domain (the "Zoom")
    x_axis = alt.X('time:T', 
                   title='Date', 
                   scale=alt.Scale(domain=[view_start, view_end]), # <--- This sets the initial zoom
                   axis=alt.Axis(format='%b %d'))

    # --- 9.3 Create a Layered Chart (Bars for Water In, Line for Water Out) ---
    
    #  ET Line (Water Loss)
    et_chart = alt.Chart(df_zoom).mark_line(strokeWidth=3, color='#FF8C00').encode(
        x=alt.X('time:T', title='Date'),
        y=alt.Y('ET0 (in):Q', title='Inches'),
        tooltip=['time:T', 'ET0 (in):Q']
    )

    #  Rain Bars (Water Gain)
    rain_chart = alt.Chart(df_zoom).mark_bar(opacity=0.5, color='#ADD8E6').encode(
        x='time:T',
        y='Rain (in):Q',
        tooltip=['time:T', 'Rain (in):Q']
    )

    #  Irrigation Bars (Water Gain)
    irr_chart = alt.Chart(df_zoom).mark_bar(size=10, color='#003366').encode(
        x='time:T',
        y='Irrigation (in):Q',
        tooltip=['time:T', 'Irrigation (in):Q']
    )

    #  Today Marker
    today_line = alt.Chart(pd.DataFrame({'time': [now_dt]})).mark_rule(
        color='red', strokeDash=[5,5], strokeWidth=2
    ).encode(x='time:T')

    # Combine all layers
    # .interactive(bind_y=False) allows scrolling through time without messsing up the Y axis scale
    final_chart = alt.layer(rain_chart, irr_chart, et_chart, today_line).properties(
        height=400
    ).interactive(bind_y=False)

    st.altair_chart(final_chart, use_container_width=True)

# Legend Key (Centered)
    st.markdown("""
    <div style="display: flex; gap: 20px; font-size: 0.8em; justify-content: center; margin-bottom: 20px;">
        <div><span style="color:#FF8C00; font-weight:bold;">━</span> ET (Loss)</div>
        <div><span style="color:#ADD8E6; font-weight:bold;">▇</span> Rain (Gain)</div>
        <div><span style="color:#003366; font-weight:bold;">▇</span> Irrigation (Gain)</div>
        <div><span style="color:red; font-weight:bold;">---</span> Today</div>
    </div>
    """, unsafe_allow_html=True)

    #### 9.5 Data Tables
with st.expander("📋 View Forecast & History Tables", expanded=False):
    tab1, tab2 = st.tabs(["🗓️ Forecast (Next 14 Days)", "📜 History (Past 90 Days)"])
    with tab1:
        st.dataframe(df_forecast.set_index("time").style.format("{:.2f}"), use_container_width=True)
    with tab2:
        st.dataframe(df_history.sort_values('time', ascending=False).set_index("time").style.format("{:.2f}"), use_container_width=True)


    
    
    
#### 10. Global Water Usage Tracker & Master Editor
st.divider()
st.header("📈 Global Water Usage Tracker")

all_logs = load_logs()

if all_logs:
    # 1. Prepare Data - Using consistent keys for the DataFrame
    combined_data = []
    for zone, events in all_logs.items():
        # Get zone profile for area/flow to calculate gallons
        z_prof = profiles.get(zone, profiles[list(profiles.keys())[0]])
        z_area = z_prof.get("area", 1000)
        z_flow = z_prof.get("flow", 5.0)
        
        for event in events:
            mins = event.get("minutes", 0)
            # Math: Gallons = Minutes * Flow Rate
            gallons_calc = mins * z_flow
            combined_data.append({
                "Date": event.get("date"),
                "Zone": zone,
                "Minutes": event.get("minutes", 0),
                "Gallons": round(gallons_calc, 1),
                "Inches": round(event.get("inches", 0), 3)
            })
    
    usage_df = pd.DataFrame(combined_data)
    
    if not usage_df.empty:
        usage_df["Date"] = pd.to_datetime(usage_df["Date"])
        usage_df = usage_df.sort_values(by="Date", ascending=False)
        total_gal = usage_df['Gallons'].sum()
        total_inches = usage_df['Inches'].sum()
        total_mins = usage_df['Minutes'].sum()

        # 2. Summary Metrics - Now using 'Minutes' which exists in the DF
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Events", len(usage_df))
        col2.metric(
            "Total Volume", 
            f"{total_gal:,.0f} gal", 
            help="Sum of all water used across all zones for this property."
        )
        col3.metric(
            "Total Depth", 
            f"{total_inches:.2f}\"", 
            help="Cumulative inches of water applied (useful for seasonal tracking)."
        )
        col4.metric(
            "Total Run Time", 
            f"{total_mins:,.0f} min"
        )

        # 3. The Master Editor Expander
        with st.expander("📂 View & Edit Full Irrigation Ledger", expanded=True):
            st.caption("Editing **Minutes** or **Zone** will automatically recalculate the **Inches** based on that Zone's profile.")
            
            edited_df = st.data_editor(
                usage_df,
                column_config={
                    "Inches": st.column_config.NumberColumn("Applied (in)", disabled=True, format="%.3f"),
                    "Gallons": st.column_config.NumberColumn("Usage (gal)", disabled=True, format="%d"),
                    "Minutes": st.column_config.NumberColumn("Minutes", min_value=0),
                    "Zone": st.column_config.SelectboxColumn("Zone", options=list(profiles.keys())),
                    "Date": st.column_config.DateColumn("Date")
                },
                num_rows="dynamic",
                use_container_width=True,
                key="global_master_editor"
            )

            # 4. Save Logic: Recalculate and Re-structure
            if not usage_df.equals(edited_df):
                new_logs = {}
                for _, row in edited_df.iterrows():
                    # --- FIX: Skip empty/invalid rows ---
                    if pd.isna(row["Date"]) or pd.isna(row["Zone"]):
                        continue
                    z_name = row["Zone"]
                    mins = row["Minutes"]
                    # Get specific profile for this row's zone to do the math accurately
                    z_prof = profiles.get(z_name, profiles[list(profiles.keys())[0]])
                    z_flow = z_prof.get("flow", 5.0)
                    z_area = z_prof.get("area", 1000)
                    
                    calc_inches = (mins * z_flow) / (z_area * 0.623)
                    
                    if z_name not in new_logs:
                        new_logs[z_name] = []
                    
                    # --- FIX: Ensure the date is a clean string ---
                    date_val = row["Date"]
                    if hasattr(date_val, "date"):
                        date_str = str(date_val.date())
                    else:
                        date_str = str(date_val)
                    
                    new_logs[z_name].append({
                        "date": str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"]),
                        "minutes": mins,
                        "inches": calc_inches
                    })
                    
                    # Double check we have data before saving
                if new_logs:
                    with open(LOG_FILE, "w") as f:
                        json.dump(new_logs, f)
                    st.success("Global logs updated!")
                    st.rerun()
                
                with open(LOG_FILE, "w") as f:
                    json.dump(new_logs, f)
                st.success("Global logs updated and inches recalculated!")
                st.rerun()

            # 5. Visual Analytics
            st.write("### 📊 Gallons Used per Zone")
            zone_usage_gal = edited_df.groupby("Zone")["Gallons"].sum()
            st.bar_chart(zone_usage_gal)
    else:
        st.info("No data found in logs.")
else:
    st.info("No watering events logged yet. Use the sidebar to log your first event!")
    
    
    
#### 11. Math & Science Expander
st.divider()
with st.expander("📚 Reference, Math & Science Methodology", expanded=False):
    tab_audit, tab_calc, tab_science, tab_refs = st.tabs([
        "📊 Soil Physics Logic", 
        "🧮 Calculation Logic", 
        "🔬 Weather Science",
        "📚 References"
    ])

with tab_audit:
    col_a, col_b = st.columns(2)
    with col_a:
        st.write("**Soil Constants**")
        st.write(f"Field Capacity (FC): {fc_inft:.2f} in/ft")
        st.write(f"Wilting Point (PWP): {pwp_inft:.2f} in/ft")
        st.write(f"Available Water (AW): {aw_per_foot:.2f} in/ft") # Updated variable
    with col_b:
        st.write("**Root Zone & Depletion**")
        st.write(f"Root Zone (RZ): {rz_ft:.2f} ft ({depth_in} in)")
        st.write(f"Plant Available Water (PAW): {paw_total:.2f} inches") # Updated variable
        st.write(f"Allowable Depletion (AD): {ad_limit:.2f} inches") # Updated variable
        
    st.divider()
    depletion_status = (current_deficit / ad_limit) * 100 if ad_limit > 0 else 0
    st.info(f"**Current Status:** Your deficit is {current_deficit:.2f}\". This is {depletion_status:.1f}% of your Allowable Depletion.")

with tab_calc:
    st.write(f"### Soil Profile: {soil_choice}")
    st.info("💡 **How we calculate your 'Soil Tank' capacity:**")
    st.latex(r"AW = FC - PWP")
    st.caption(f"Available Water: {fc_inft/12:.3f} - {pwp_inft/12:.3f} = **{aw_per_foot/12:.3f} in/in**")
    st.latex(r"PAW = AW \times RZ")
    st.caption(f"Plant Available Water: {aw_per_foot:.2f} in/ft × {rz_ft:.2f} ft = **{paw_total:.2f} inches**")
    st.latex(r"AD = PAW \times MAD")
    st.caption(f"Allowable Depletion: {paw_total:.2f} in × {mad/100:.2f} = **{ad_limit:.2f} inches**")
    st.divider()
    
    st.table(pd.DataFrame({
        "Parameter": ["AWC (Soil Capacity)", "Root Depth", "Total Tank Size (PAW)", "MAD (Buffer)", "Allowable Depletion (AD)"],
        "Value": [f"{(aw_per_foot/12):.3f} in/in", f"{depth_in} in", f"{paw_total:.2f} in", f"{mad}%", f"{ad_limit:.2f} in"]
    }))
    st.divider()
    st.write("### Volume & Runtime Logic")
    st.markdown(f"""
    1. **Net Depth Needed:** **{current_deficit:.3f}"**
    2. **Water Volume:** {current_deficit:.3f}" × {area} sq ft × 0.623 = **{gallons:.1f} Gallons**
    3. **Runtime:** {gallons:.1f} gal / {flow} GPM = **{runtime:.1f} Minutes**
    """)

with tab_science:
    st.write("### Evapotranspiration (ET₀) Explained")
    
    st.markdown("""
    The "Water Loss" value is **ET₀** (Reference Evapotranspiration), calculated via the **FAO-56 Penman-Monteith equation**.
    
    **Environmental Factors Used:**
    1. **Solar Radiation:** Energy for evaporation.
    2. **Temperature:** High heat increases atmospheric pull.
    3. **Humidity:** Drier air accelerates water loss.
    4. **Wind:** Removes the humid layer around leaves.
    """)
    st.info("Data Source: Open-Meteo API using high-resolution weather models.")
    
with tab_refs:
    st.write("### References")
    st.markdown("""
    **Reference:** Evaluating Field Capacity, Wilting Point, Saturation, and Plant Available Water
    
        Saxton, Keith S., and Walter J. Rawls. 'Estimating Soil Water 
        Characteristics from Texture, Organic Matter, and Salinity.' *Soil Science 
        Society of America Journal*, vol. 70, no. 5, 2006, pp. 1569-1578.
    
    **Reference:** FAO-56 Penman-Monteith Evaluating Evapotranspiration
    
        Allen, Richard G., et al. 'Crop Evapotranspiration: Guidelines for 
        Computing Crop Water Requirements.' *FAO Irrigation and Drainage Paper 56*, 1998.
    """)
    
    
    
    
#### 99. Backup Utility
import shutil

st.divider()
with st.expander("🛡️ Data Security & Backups"):
    st.write("Click below to create a timestamped clone of all properties, zones, and history.")
    
    if st.button("🚀 Create Instant Backup"):
        # Create a unique folder name: e.g., "Backup_2024-05-20_14-30"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        current_backup_path = os.path.join(BACKUP_DIR, f"Backup_{timestamp}")
        
        try:
            # Copy the entire IrrigationData folder to the new backup location
            shutil.copytree(DATA_DIR, current_backup_path)
            st.success(f"Backup Successful! Files stored in: `{current_backup_path}`")
            
            # List current backups
            st.write("### Recent Backups on Disk:")
            all_backups = sorted(os.listdir(BACKUP_DIR), reverse=True)
            for b in all_backups[:5]: # Show last 5
                st.text(f"📁 {b}")
                
        except Exception as e:
            st.error(f"Backup failed: {e}")

    st.caption("Note: This creates a local copy on your machine. For extra safety, copy the 'Backups' folder to a cloud drive.")
