import streamlit as st
import pandas as pd
import requests
import json
import os
from datetime import datetime

#### 1. Page Configuration
st.set_page_config(page_title="Irrigation Master Pro", layout="wide", page_icon="🌱")
st.title("🌱 Precision Irrigation:Water Balance")

#### 2. Profile Management
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "IrrigationData")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "zone_profiles.json")
LOG_FILE = os.path.join(DATA_DIR, "irrigation_log.json")
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
    logs[zone].append({
        "date": str(datetime.now().date()),
        "minutes": minutes,
        "inches": inches_applied
    })
    with open(LOG_FILE, "w") as f: json.dump(logs, f)

profiles = load_profiles()

WEATHER_LOG = os.path.join(DATA_DIR, "weather_history.json")

def archive_weather(df_daily):
    """Saves daily ET and Rain to a local JSON so history builds over time."""
    history = {}
    if os.path.exists(WEATHER_LOG):
        with open(WEATHER_LOG, "r") as f:
            history = json.load(f)
    
    # Add new dates from the current weather fetch
    for _, row in df_daily.iterrows():
        date_str = row['time'].strftime('%Y-%m-%d')
        # We only save past dates or today to the permanent log
        if row['time'].date() <= datetime.now().date():
            history[date_str] = {
                "ET0 (in)": row["ET0 (in)"],
                "Rain (in)": row["Rain (in)"]
            }
            
    with open(WEATHER_LOG, "w") as f:
        json.dump(history, f)

def load_weather_history():
    if os.path.exists(WEATHER_LOG):
        with open(WEATHER_LOG, "r") as f:
            data = json.load(f)
            df = pd.DataFrame.from_dict(data, orient='index').reset_index()
            df.columns = ['time', 'ET0 (in)', 'Rain (in)']
            df['time'] = pd.to_datetime(df['time'])
            return df
    return pd.DataFrame(columns=['time', 'ET0 (in)', 'Rain (in)'])

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
st.sidebar.header("📁 Zone Profiles")
zone_list = list(profiles.keys())
if not zone_list: zone_list = ["Default Zone"]

selected_zone_name = st.sidebar.selectbox("Select Active Zone", zone_list)
current_zone = profiles.get(selected_zone_name, profiles[zone_list[0]])

# Input for creating a new zone name
new_zone_name = st.sidebar.text_input("New Zone Name (Optional)", placeholder="e.g. Backyard")

with st.sidebar.expander("📍 Edit Location", expanded=False):
    default_zip = current_zone.get("zip", "48892")
    zip_input = st.text_input("Zip Code", value=str(default_zip))

@st.cache_data(ttl=3600)
def get_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5).json()
        return float(res['places'][0]['latitude']), float(res['places'][0]['longitude']), f"{res['places'][0]['place name']}"
    except Exception: 
        return 42.9286, -84.7981, "Westphalia, MI"

lat, lon, z_name = get_coords(zip_input)

st.sidebar.header("💧 Irrigation Specs")
area = st.sidebar.number_input("Zone Area (sq ft)", value=float(current_zone.get("area", 1000)))
flow = st.sidebar.number_input("Zone Flow (GPM)", value=float(current_zone.get("flow", 5.0)))

soil_types = ["Sand", "Loamy Sand", "Sandy Loam", "Loam", "Silt Loam", "Clay Loam", "Clay"]
saved_soil = current_zone.get("soil", "Loam")
soil_index = soil_types.index(saved_soil) if saved_soil in soil_types else 3

soil_choice = st.sidebar.selectbox("Soil", soil_types, index=soil_index)
depth_in = st.sidebar.slider("Root Depth (in)", 4, 36, int(current_zone.get("depth", 12)))
mad = st.sidebar.slider("MAD (%)", 10, 60, int(current_zone.get("mad", 50)))

if st.sidebar.button("💾 Save Zone Profile"):
    target_name = new_zone_name if new_zone_name.strip() != "" else selected_zone_name
    profiles[target_name] = {
        "zip": zip_input, 
        "area": area, 
        "flow": flow, 
        "soil": soil_choice, 
        "depth": depth_in, 
        "mad": mad
    }
    save_profiles(profiles)
    st.sidebar.success(f"Saved profile: {target_name}")
    st.rerun()
    
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
@st.cache_data(ttl=3600)
def fetch_weather(l, n):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={l}&longitude={n}&hourly=et0_fao_evapotranspiration,precipitation&timezone=auto&past_days=7&forecast_days=14"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else None

weather = fetch_weather(lat, lon)

if weather and "hourly" in weather:
    # Build Dataframe
    df_api = pd.DataFrame({
        "time": pd.to_datetime(weather["hourly"]["time"]),
        "ET0 (in)": [v / 25.4 for v in weather["hourly"]["et0_fao_evapotranspiration"]],
        "Rain (in)": [v / 25.4 for v in weather["hourly"]["precipitation"]]
    }).set_index("time").resample('D').sum().reset_index()
    
    archive_weather(df_api)
    df_permanent = load_weather_history()
    df_daily = pd.concat([df_permanent, df_api]).drop_duplicates(subset='time', keep='last').sort_values('time')
    start_date = pd.to_datetime(datetime.now().date()) - pd.Timedelta(days=7)
    df_daily = df_daily[df_daily['time'] >= start_date]
    
    # --- ADDED: Process Irrigation Logs for Graph ---
    zone_logs = load_logs().get(selected_zone_name, [])
    
    # --- ZONE-SPECIFIC IRRIGATION LOGS ---
    all_logs = load_logs()
    # Filter logs strictly for the selected zone
    zone_logs = all_logs.get(selected_zone_name, [])
    
    # Convert logs list to a temporary DataFrame
    if zone_logs:
        log_df = pd.DataFrame(zone_logs)
        log_df['time'] = pd.to_datetime(log_df['date'])
        # Sum irrigation by date (in case you water twice in one day)
        log_daily = log_df.groupby('time')['inches'].sum().reset_index()
        log_daily.columns = ['time', 'Irrigation (in)']
        # Merge logs into our main daily dataframe
        df_daily = pd.merge(df_daily, log_daily, on='time', how='left').fillna(0)
    else:
        df_daily['Irrigation (in)'] = 0.0
    # -----------------------------------------------

    # Identify Today & History
    today_dt = pd.to_datetime(datetime.now().date())
    df_history = df_daily[df_daily['time'] < today_dt].copy()
    df_forecast = df_daily[df_daily['time'] >= today_dt].copy()
    

    
#### 7. DEFICIT CALCULATION
    # Calculate historical debt (Past 7 days)
    hist_et, hist_rain = df_history['ET0 (in)'].sum(), df_history['Rain (in)'].sum()
    
    # Calculate today's current status
    today_et, today_rain = df_forecast.iloc[0]['ET0 (in)'], df_forecast.iloc[0]['Rain (in)']
    
    # Get all irrigation logged for this zone in the last 7 days + today
    recent_irrigation = df_history['Irrigation (in)'].sum() + df_forecast.iloc[0]['Irrigation (in)']
    
    # NEW LOGIC: Total Need = (Historical Loss + Today's Loss) - (Rain + Irrigation)
    total_losses = hist_et + today_et
    total_gains = hist_rain + today_rain + recent_irrigation
    
    # Calculate the remaining deficit
    current_deficit = max(0, total_losses - total_gains)
    
    # Calculation for Volume and Runtime
    gallons =  current_deficit * area * 0.623
    runtime = gallons / flow if flow > 0 else 0
    
    
    
    
#### 8. Dashboard Metrics
    st.subheader(f"Zone: {selected_zone_name} ({z_name})")
    seven_day_et = df_forecast.iloc[0:7]['ET0 (in)'].sum()
    seven_day_rain = df_forecast.iloc[0:7]['Rain (in)'].sum()
    today_et = df_forecast.iloc[0]['ET0 (in)']
    today_rain = df_forecast.iloc[0]['Rain (in)']
    m1, m2, m3, m4 = st.columns(4)
    
    # Metric 1 is now 7-Day ET
    m1.metric("7-Day ET Forecast", f"{seven_day_et:.2f}\"", delta=f"Today: {today_et:.2f}\"", delta_color="inverse")
    
    # Metric 2 7-Day Rain
    m2.metric("7-Day Rain Forecast", f"{seven_day_rain:.2f}\"", delta=f"Today: {today_rain:.2f}\"")
    
    # Metric 3 is now the Water to Apply (Depth, Time, and Vol)
    m3.metric("Water to Apply", f"{ current_deficit:.2f}\"", 
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

    m4.metric(
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
  
  
  
  
#### 9. Graphs & Tables
    st.write(f"### 📈 21-Day Water Balance for {selected_zone_name}")
    
    # Custom Colors: Orange for ET, Light Blue for Rain, Green for Irrigation
    color_map = {
        "ET0 (in)": "#FF8C00",      # Dark Orange
        "Rain (in)": "#ADD8E6",     # Light Blue
        "Irrigation (in)": "#003366" # Shutterstock Dark Blue
    }
    
    st.line_chart(
        df_daily.set_index("time")[["ET0 (in)", "Rain (in)", "Irrigation (in)"]],
        color=[color_map[c] for c in ["ET0 (in)", "Rain (in)", "Irrigation (in)"]]
    )
    

    
    # Correcting the Display formatting
    df_forecast_display = df_forecast.copy()
    df_forecast_display['time'] = df_forecast_display['time'].dt.strftime('%B %d, %Y')
    
    df_history_display = df_history.copy()
    df_history_display['time'] = df_history_display['time'].dt.strftime('%B %d, %Y')
    
    tab1, tab2 = st.tabs(["🗓️ Forecast (Next 14 Days)", "📜 History (Past 7 Days)"])
    with tab1:
        # Crucial fix: using the _display dataframe here
        st.dataframe(df_forecast_display.set_index("time").style.format("{:.2f}"), use_container_width=True)
    with tab2:
        # Crucial fix: using the _display dataframe here
        st.dataframe(df_history_display.set_index("time").style.format("{:.2f}"), use_container_width=True)

    
    
    

    
    
    
#### 12. Global Water Usage Tracker & Master Editor
st.divider()
st.header("📈 Global Water Usage Tracker")

all_logs = load_logs()

if all_logs:
    # 1. Prepare Data - Using consistent keys for the DataFrame
    combined_data = []
    for zone, events in all_logs.items():
        for event in events:
            combined_data.append({
                "Date": event.get("date"),
                "Zone": zone,
                "Minutes": event.get("minutes", 0),
                "Inches": round(event.get("inches", 0), 3)
            })
    
    usage_df = pd.DataFrame(combined_data)
    
    if not usage_df.empty:
        usage_df["Date"] = pd.to_datetime(usage_df["Date"])
        usage_df = usage_df.sort_values(by="Date", ascending=False)

        # 2. Summary Metrics - Now using 'Minutes' which exists in the DF
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Events Logged", len(usage_df))
        col2.metric("Total Run Time", f"{usage_df['Minutes'].sum():,.0f} min")
        col3.metric("Zones Tracked", len(all_logs.keys()))

        # 3. The Master Editor Expander
        with st.expander("📂 View & Edit Full Irrigation Ledger", expanded=True):
            st.caption("Editing **Minutes** or **Zone** will automatically recalculate the **Inches** based on that Zone's profile.")
            
            edited_df = st.data_editor(
                usage_df,
                column_config={
                    "Inches": st.column_config.NumberColumn("Applied (in)", disabled=True, format="%.3f"),
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
                    z_name = row["Zone"]
                    mins = row["Minutes"]
                    # Get specific profile for this row's zone to do the math accurately
                    z_prof = profiles.get(z_name, profiles[list(profiles.keys())[0]])
                    z_flow = z_prof.get("flow", 5.0)
                    z_area = z_prof.get("area", 1000)
                    
                    calc_inches = (mins * z_flow) / (z_area * 0.623)
                    
                    if z_name not in new_logs:
                        new_logs[z_name] = []
                    
                    new_logs[z_name].append({
                        "date": str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"]),
                        "minutes": mins,
                        "inches": calc_inches
                    })
                
                with open(LOG_FILE, "w") as f:
                    json.dump(new_logs, f)
                st.success("Global logs updated and inches recalculated!")
                st.rerun()

            # 5. Visual Analytics
            st.write("### Usage by Zone (Total Inches)")
            zone_totals = edited_df.groupby("Zone")["Inches"].sum()
            st.bar_chart(zone_totals)
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
