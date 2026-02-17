import streamlit as st
import pandas as pd
import requests
import json
import os
import altair as alt
from datetime import datetime

#### 1. Page Configuration
st.set_page_config(page_title="Irrigation Master Pro", layout="wide", page_icon="🌱")
st.title("🌱 Precision Irrigation:Water Balance")

#### 2 Profile and Property Selection
# --- ADDED: PROPERTY SELECTION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "IrrigationData")
SYSTEM_DIR = os.path.join(DATA_DIR, "SystemData")
BACKUP_DIR = os.path.join(BASE_DIR, "Backups")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(SYSTEM_DIR, exist_ok=True)
PROP_LIST_FILE = os.path.join(SYSTEM_DIR, "properties.json")

def load_properties_master():
    if os.path.exists(PROP_LIST_FILE):
        try:
            with open(PROP_LIST_FILE, "r") as f: 
                return json.load(f)
        except: 
            pass
    return {"Home": "48892"}

def save_properties_master(props_dict):
    with open(PROP_LIST_FILE, "w") as f: 
        json.dump(props_dict, f)

# Initialize Session State
if 'prop_master' not in st.session_state:
    st.session_state.prop_master = load_properties_master()

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
                             

# Display the title once
st.title(f"🌱 {active_prop} ({active_zip})")

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
soil_types = ["Sand", "Loamy Sand", "Sandy Loam", "Loam", "Silt Loam", "Clay Loam", "Clay"]
saved_soil = current_zone.get("soil", "Loam")
soil_index = soil_types.index(saved_soil) if saved_soil in soil_types else 3

soil_choice = st.sidebar.selectbox("Soil", soil_types, index=soil_index)
depth_in = st.sidebar.slider("Root Depth (in)", 4, 36, int(current_zone.get("depth", 12)))
mad = st.sidebar.slider("MAD (%)", 10, 60, int(current_zone.get("mad", 50)))

save_col, del_col = st.sidebar.columns(2)
if save_col.button("💾 Save Zone"):
    target_name = new_zone_name if new_zone_name.strip() != "" else selected_zone_name
    profiles[target_name] = {"area": area, "flow": flow, "soil": soil_choice, "depth": depth_in, "mad": mad}
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



#### 5. MATH ENGINE (Rosetta Logic)
phys = SOIL_DATA[soil_choice]
fc_inft = phys["FC"]
pwp_inft = phys["PWP"]

#### AW = FC - PWP (in in/ft)
aw_inft = fc_inft - pwp_inft

# PAW = AW * RZ (RZ converted to feet)
rz_ft = depth_in / 12
paw_inches = aw_inft * rz_ft

# AD = PAW * MAD
allowable_depletion = paw_inches * (mad / 100)

#### 6. WEATHER ENGINE
def archive_weather(df_daily):
    history = {}
    if os.path.exists(WEATHER_LOG):
        with open(WEATHER_LOG, "r") as f: history = json.load(f)
    for _, row in df_daily.iterrows():
        date_str = row['time'].strftime('%Y-%m-%d')
        if row['time'].date() <= datetime.now().date():
            history[date_str] = {"ET0 (in)": row["ET0 (in)"], "Rain (in)": row["Rain (in)"]}
    with open(WEATHER_LOG, "w") as f: json.dump(history, f)

@st.cache_data(ttl=3600)
def fetch_weather(l, n):
    # Pull 92 days of history to maximize Open-Meteo's free tier
    url = f"https://api.open-meteo.com/v1/forecast?latitude={l}&longitude={n}&hourly=et0_fao_evapotranspiration,precipitation&timezone=auto&past_days=92&forecast_days=14"
    try:
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None

weather = fetch_weather(lat, lon)

if weather and "hourly" in weather:
    # --- FIXED: None-handling logic to prevent TypeError ---
    df_api = pd.DataFrame({
        "time": pd.to_datetime(weather["hourly"]["time"]),
        "ET0 (in)": [v / 25.4 if v is not None else 0.0 for v in weather["hourly"]["et0_fao_evapotranspiration"]],
        "Rain (in)": [v / 25.4 if v is not None else 0.0 for v in weather["hourly"]["precipitation"]]
    }).set_index("time").resample('D').sum().reset_index()
    
    archive_weather(df_api)
    df_permanent = load_weather_history()
    df_daily = pd.concat([df_permanent, df_api]).drop_duplicates(subset='time', keep='last').sort_values('time')
    
    # Filter to 90 days
    start_date = pd.to_datetime(datetime.now().date()) - pd.Timedelta(days=90)
    df_daily = df_daily[df_daily['time'] >= start_date]
    
    # --- ZONE-SPECIFIC IRRIGATION LOGS ---
    all_logs = load_logs()
    zone_logs = all_logs.get(selected_zone_name, [])
    
    if zone_logs:
        log_df = pd.DataFrame(zone_logs)
        log_df['time'] = pd.to_datetime(log_df['date'])
        log_daily = log_df.groupby('time')['inches'].sum().reset_index()
        log_daily.columns = ['time', 'Irrigation (in)']
        df_daily = pd.merge(df_daily, log_daily, on='time', how='left').fillna(0)
    else:
        df_daily['Irrigation (in)'] = 0.0

    # Data Cleanup
    df_daily['time'] = pd.to_datetime(df_daily['time']).dt.normalize()
    for col in ["ET0 (in)", "Rain (in)", "Irrigation (in)"]:
        df_daily[col] = pd.to_numeric(df_daily[col], errors='coerce').fillna(0.0)

    # Split History and Forecast
    today_dt = pd.Timestamp(datetime.now().date())
    df_history = df_daily[df_daily['time'] < today_dt].copy()
    df_forecast = df_daily[df_daily['time'] >= today_dt].copy()

#### 7. DEFICIT CALCULATION
    # Calculate the actual current deficit based on the full 90-day window
    total_et = df_daily[df_daily['time'] <= today_dt]['ET0 (in)'].sum()
    total_gains = df_daily[df_daily['time'] <= today_dt]['Rain (in)'].sum() + \
                  df_daily[df_daily['time'] <= today_dt]['Irrigation (in)'].sum()
    
    current_deficit = max(0, total_et - total_gains)

    # Calculate Core Metrics
    gallons = current_deficit * area * 0.623
    runtime = gallons / flow if flow > 0 else 0

    # Smart Toggle Reset Logic
    st.sidebar.divider()
    st.sidebar.subheader("🔄 Soil Calibration")

    # Check if a reset was already performed for THIS zone TODAY
    today_str = str(datetime.now().date())
    existing_reset_index = None
    
    # We look for any entry today where minutes == 0 (our "Reset" signature)
    for i, entry in enumerate(zone_logs):
        if entry.get("date") == today_str and entry.get("minutes") == 0:
            existing_reset_index = i
            break

    if existing_reset_index is None:
        # STATE A: No reset today -> Show the "Saturated" button
        if st.sidebar.button("Full Reset: Soil is Saturated", help="Sets deficit to zero", use_container_width=True):
            if current_deficit > 0:
                # Logs 0 mins but applies exactly enough inches to zero out the deficit
                save_log(selected_zone_name, 0, current_deficit)
                st.rerun()
            else:
                st.sidebar.info("Soil is already full.")
    else:
        # STATE B: Reset found -> Show the "Undo" button
        st.sidebar.warning("Soil was marked as Saturated today.")
        if st.sidebar.button("🔙 Undo Today's Reset", type="primary", use_container_width=True):
            all_logs = load_logs()
            if selected_zone_name in all_logs:
                # Remove the 0-minute entry for today from the file
                all_logs[selected_zone_name] = [
                    log for log in all_logs[selected_zone_name] 
                    if not (log.get("date") == today_str and log.get("minutes") == 0)
                ]
                with open(LOG_FILE, "w") as f:
                    json.dump(all_logs, f)
                st.rerun()
        
    
    
    
#### 8. Dashboard Metrics
    st.subheader(f"Zone: {selected_zone_name} ({z_name})")
    seven_day_et = df_forecast.iloc[0:7]['ET0 (in)'].sum()
    seven_day_rain = df_forecast.iloc[0:7]['Rain (in)'].sum()
    today_et = df_forecast.iloc[0]['ET0 (in)']
    today_rain = df_forecast.iloc[0]['Rain (in)']
    m1, m2, m3, m4 = st.columns(4)
    
    # Metric 1 is now 7-Day ET
    m3.metric("7-Day ET Forecast", f"{seven_day_et:.2f}\"", delta=f"Today: {today_et:.2f}\"", delta_color="inverse")
    
    # Metric 2 7-Day Rain
    m4.metric("7-Day Rain Forecast", f"{seven_day_rain:.2f}\"", delta=f"Today: {today_rain:.2f}\"")
    
    # Metric 3 is now the Water to Apply (Depth, Time, and Vol)
    m1.metric("Water to Apply", f"{ current_deficit:.2f}\"", 
              delta=f"{runtime:.1f} min ({gallons:.0f} gal)")
     
    # Metric 4: Allowable Depletion & Status Arrow [Logic: If deficit is less than AD, we are "OK" (Green). If deficit > AD, we are "Over" (Red).]
    if current_deficit < allowable_depletion:
        status_msg = "Wait to Apply"
        d_val = "OK"
        d_color = "normal" # Shows Green
    else:
        status_msg = "Water Needed"
        d_val = "LOW"
        d_color = "inverse" # Shows Red

    m2.metric(
        label="Allowable Depletion", 
        value=f"{allowable_depletion:.2f}\"", 
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
st.write(f"### 📈 Water Balance for {selected_zone_name}")

if not df_daily.empty:
    # 9.2 Setup Time Window (The "Secret Sauce" that fixed the blank screen)
    now_dt = pd.Timestamp(datetime.now().date())
    zoom_start = now_dt - pd.Timedelta(days=7)
    zoom_end = now_dt + pd.Timedelta(days=14)
    
    # Filter the data frame first
    df_zoom = df_daily[(df_daily['time'] >= zoom_start) & (df_daily['time'] <= zoom_end)].copy()

    # 9.3 Melt the data (This creates the legend automatically)
    # We turn ET, Rain, and Irrigation columns into one 'Inches' column and one 'Type' label column
    df_melted = df_zoom.melt(
        id_vars=['time'], 
        value_vars=['ET0 (in)', 'Rain (in)', 'Irrigation (in)'], 
        var_name='Type', 
        value_name='Inches'
    )

    # 9.4 Create the Chart
    # By encoding 'Type' to Color, Altair builds the legend for us.
    main_chart = alt.Chart(df_melted).mark_line(strokeWidth=3).encode(
        x=alt.X('time:T', title='Date', axis=alt.Axis(format='%b %d')),
        y=alt.Y('Inches:Q', title='Inches'),
        color=alt.Color('Type:N', 
            scale=alt.Scale(
                domain=['ET0 (in)', 'Rain (in)', 'Irrigation (in)'],
                range=['#FF8C00', '#ADD8E6', '#003366']
            ),
            legend=alt.Legend(orient="top", title=None)
        ),
        tooltip=['time:T', 'Type:N', 'Inches:Q']
    )

    # The Red "Today" Line
    today_line = alt.Chart(pd.DataFrame({'time': [now_dt]})).mark_rule(
        color='red', strokeDash=[5,5], strokeWidth=2
    ).encode(x='time:T')

    # Combine and Display
    final_chart = (main_chart + today_line).properties(
        height=400
    ).interactive()

    st.altair_chart(final_chart, use_container_width=True)

    #### 9.5 Data Tables
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
st.subheader("📚 Reference & Methodology")

tab_audit, tab_calc, tab_science = st.tabs([
    "📊 Soil Physics Audit", 
    "🧮 Calculation Logic", 
    "🔬 Weather Science"
])

with tab_audit:
    col_a, col_b = st.columns(2)
    with col_a:
        st.write("**Soil Constants**")
        st.write(f"Field Capacity (FC): {fc_inft:.2f} in/ft")
        st.write(f"Wilting Point (PWP): {pwp_inft:.2f} in/ft")
        st.write(f"Available Water (AW): {aw_inft:.2f} in/ft")
    with col_b:
        st.write("**Root Zone & Depletion**")
        st.write(f"Root Zone (RZ): {rz_ft:.2f} ft ({depth_in} in)")
        st.write(f"Plant Available Water (PAW): {paw_inches:.2f} inches")
        st.write(f"Allowable Depletion (AD): {allowable_depletion:.2f} inches")
    
    st.divider()
    depletion_status = (current_deficit / allowable_depletion) * 100 if allowable_depletion > 0 else 0
    st.info(f"**Current Status:** Your deficit is {current_deficit:.2f}\". This is {depletion_status:.1f}% of your Allowable Depletion.")

with tab_calc:
    st.write(f"### Soil Profile: {soil_choice}")
    st.table(pd.DataFrame({
        "Parameter": ["AWC (Soil Capacity)", "Root Depth", "Total Tank Size (PAW)", "MAD (Buffer)", "Allowable Depletion (AD)"],
        "Value": [f"{(aw_inft/12):.3f} in/in", f"{depth_in} in", f"{paw_inches:.2f} in", f"{mad}%", f"{allowable_depletion:.2f} in"]
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
