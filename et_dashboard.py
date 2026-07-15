import os
import json
import requests
import requests_cache
import platform
import shutil
from datetime import datetime
import pandas as pd
import altair as alt
import streamlit as st
from sqlalchemy import text
from retry_requests import retry
import openmeteo_requests

# Import local business modules
import core_logic
import data_manager

# ==============================================================================
# 0. CONSTANTS, INITIALIZATION & HELPER FUNCTIONS
# ==============================================================================

# 0.1 Session States & Property Settings
if "user_id" not in st.session_state:
    st.session_state.user_id = "default_user"

# Load the master property list
properties_dict = data_manager.load_json(data_manager.PROP_LIST_FILE, {"My Property": "48894"})

# Ensure PROP_LIST_FILE exists on disk
if not os.path.exists(data_manager.PROP_LIST_FILE):
    data_manager.save_properties_master(properties_dict)

# Render Property Selector in Sidebar
st.sidebar.header("🏠 Property Settings")

prop_options = list(properties_dict.keys())
if "active_prop" not in st.session_state or st.session_state.active_prop not in prop_options:
    st.session_state.active_prop = prop_options[0]

active_prop = st.sidebar.selectbox("Select Active Property", prop_options, index=prop_options.index(st.session_state.active_prop))
st.session_state.active_prop = active_prop
active_zip = properties_dict[active_prop]

# Expandable area to add a new property
with st.sidebar.expander("➕ Add New Property"):
    new_prop_name = st.text_input("Property Name", key="new_prop_name_input")
    new_prop_zip = st.text_input("Zip Code", key="new_prop_zip_input")
    if st.button("Save Property", use_container_width=True):
        if new_prop_name.strip() and new_prop_zip.strip():
            p_name = new_prop_name.strip()
            p_zip = new_prop_zip.strip()
            if p_name not in properties_dict:
                properties_dict[p_name] = p_zip
                data_manager.save_properties_master(properties_dict)
                st.session_state.active_prop = p_name
                st.toast(f"🏡 Property '{p_name}' added!")
                st.rerun()
            else:
                st.error("A property with that name already exists.")

# Dynamic path resolution based on active property
paths = data_manager.get_prop_paths(active_prop)
PROFILE_FILE = paths["db"]
LOG_FILE = paths["log"]
WEATHER_LOG = paths["weather"]
DATA_DIR = data_manager.DATA_DIR
BACKUP_DIR = data_manager.BACKUP_DIR


# 0.3 Setup Database Connection (Fallback to Streamlit SQL Connection)
try:
    conn = st.connection("postgresql", type="sql")
except Exception:
    # Safe local fallback Mock if database credentials are not present in secrets.toml
    class MockConnection:
        def query(self, query, params=None, ttl=0):
            # Return empty DataFrame if DB isn't configured so local JSON can take over
            return pd.DataFrame(columns=['zone_name', 'log_date', 'minutes', 'inches'])
        class MockSession:
            def execute(self, *args, **kwargs): pass
            def commit(self): pass
        session = MockSession()
    conn = MockConnection()

# 0.4 Helper: Local Zone Profiles I/O using data_manager
def load_profiles():
    """Loads configured zone settings from local storage or returns a default layout."""
    default_profile = {
        "Front Lawn": {
            "zip": active_zip,
            "area": 1200,
            "flow": 8.0,
            "soil": "Loam",
            "depth": 6,
            "mad": 50,
            "start_date": str(datetime.now().date() - pd.Timedelta(days=7))
        }
    }
    return data_manager.load_json(PROFILE_FILE, default_profile)

def save_profiles(profiles_dict):
    """Saves structural zone configurations to local JSON."""
    data_manager.save_json(PROFILE_FILE, profiles_dict)



# ==============================================================================
# 1. DATABASE & STORAGE UTILITIES
# ==============================================================================

# 1.1 Load Zone Logs from Database
def load_logs():
    """Pulls watering history matching this property and active user."""
    query = """
        SELECT zone_name, log_date, minutes, inches 
        FROM watering_logs 
        WHERE property = :prop AND user_id = :user_id
    """
    try:
        df = conn.query(query, params={"prop": active_prop, "user_id": st.session_state.user_id}, ttl=0)
    except Exception:
        df = pd.DataFrame()
    
    # Local JSON Backup Fallback if Database isn't reachable
    if df.empty:
        local_data = data_manager.load_json(LOG_FILE, {})
        combined_list = []
        for z, events in local_data.items():
            for ev in events:
                combined_list.append({
                    "zone_name": z,
                    "log_date": ev["date"],
                    "minutes": ev["minutes"],
                    "inches": ev["inches"]
                })
        df = pd.DataFrame(combined_list)

    logs = {}
    if not df.empty:
        for _, row in df.iterrows():
            z = row['zone_name']
            if z not in logs: 
                logs[z] = []
            logs[z].append({
                "date": str(row['log_date']), 
                "minutes": row['minutes'], 
                "inches": row['inches']
            })
    return logs


# 1.2 Save Local Log to Database & JSON Backup
def save_log(zone, minutes, inches_applied):
    """Inserts a verified watering event into PostgreSQL and mirrors to local backup."""
    # Step A: Push to SQL Connection if online
    try:
        with conn.session as session:
            query = text("""
                INSERT INTO watering_logs (user_id, property, zone_name, log_date, minutes, inches)
                VALUES (:user_id, :prop, :zone, :date, :mins, :inches);
            """)
            session.execute(
                query,
                {
                    "user_id": st.session_state.user_id,
                    "prop": active_prop,
                    "zone": zone,
                    "date": datetime.now().date(),
                    "mins": float(minutes),
                    "inches": float(inches_applied)
                }
            )
            session.commit()
    except Exception:
        pass

    # Step B: Mirror to local backup JSON
    local_logs = data_manager.load_json(LOG_FILE, {})
    if zone not in local_logs:
        local_logs[zone] = []
    local_logs[zone].append({
        "date": str(datetime.now().date()),
        "minutes": float(minutes),
        "inches": float(inches_applied)
    })
    data_manager.save_json(LOG_FILE, local_logs)

    st.cache_data.clear()


# 1.3 Archive Weather to Local Storage Cache
def archive_weather(df_daily):
    """Stores historical daily ET and Rainfall records in a local JSON structure."""
    history = data_manager.load_json(WEATHER_LOG, {})
    for _, row in df_daily.iterrows():
        date_str = row['time'].strftime('%Y-%m-%d')
        if row['time'].date() <= datetime.now().date():
            history[date_str] = {"ET0 (in)": row["ET0 (in)"], "Rain (in)": row["Rain (in)"]}
    data_manager.save_json(WEATHER_LOG, history)


# 1.4 Load Archived Weather History
def load_weather_history():
    """Loads archived local weather logs and transforms into a DataFrame."""
    data = data_manager.load_json(WEATHER_LOG, {})
    if data:
        df = pd.DataFrame.from_dict(data, orient='index').reset_index()
        df.columns = ['time', 'ET0 (in)', 'Rain (in)']
        df['time'] = pd.to_datetime(df['time'])
        return df
    return pd.DataFrame(columns=['time', 'ET0 (in)', 'Rain (in)'])


# ==============================================================================
# 2. APPLICATION INITIALIZATION
# ==============================================================================

# 2.1 Load Local App Profiles
profiles = load_profiles()


# ==============================================================================
# 3. ZONE SELECTION & MANAGEMENT
# ==============================================================================
st.sidebar.header(f"📍 {active_prop} Zones")

# Extract the current active zones
zone_list = list(profiles.keys())

# 3.1 Fallback: Create Default Zone if empty
if not zone_list:
    st.sidebar.warning("No zones found for this property.")
    if st.sidebar.button("➕ Initialize Default Zone 1", use_container_width=True):
        profiles["Zone 1"] = {
            "zip": active_zip,
            "area": 1000,
            "flow": 5,
            "soil": "Loam",
            "depth": 6,
            "mad": 50,
            "start_date": str(datetime.now().date())
        }
        save_profiles(profiles)
        st.rerun()
    st.stop()


# 3.2 Action Callback: Rename Active Zone
def handle_rename_submit():
    new_name = st.session_state.rename_input.strip()
    if new_name and new_name != active_zone_name:
        profiles[new_name] = profiles.pop(active_zone_name)
        
        # Sync naming modification inside PostgreSQL logs
        try:
            with conn.session as session:
                session.execute(
                    text("""
                        UPDATE watering_logs 
                        SET zone_name = :new_name 
                        WHERE property = :prop AND zone_name = :old_name AND user_id = :user_id
                    """),
                    {
                        "new_name": new_name, 
                        "old_name": active_zone_name, 
                        "prop": active_prop, 
                        "user_id": st.session_state.user_id
                    }
                )
                session.commit()
        except Exception:
            pass
        
        save_profiles(profiles)
        st.toast(f"✏️ Renamed to {new_name}!")
        st.rerun()


# 3.3 Action Callback: Add New Custom Zone
def handle_add_submit():
    custom_new_zone = st.session_state.add_input.strip()
    if custom_new_zone and custom_new_zone not in profiles:
        profiles[custom_new_zone] = {
            "zip": active_zip,
            "area": 1000,
            "flow": 5,
            "soil": "Loam",
            "depth": 12,
            "mad": 50,
            "start_date": str(datetime.now().date())
        }
        save_profiles(profiles)
        st.toast(f"🌱 {custom_new_zone} added successfully!")
        st.rerun()


# 3.4 Display Zone Selection UI Columns
zone_col1, zone_col2 = st.sidebar.columns([3, 1]) 

with zone_col1:
    active_zone_name = st.selectbox("Select Active Zone", zone_list, label_visibility="visible")
    current_zone = profiles[active_zone_name]

with zone_col2:
    st.write(" ")
    st.write(" ")
    with st.popover("⚙️"):
        # SECTION A: RENAME ZONE
        st.subheader("📝 Rename This Zone")
        st.text_input(
            "New Name", 
            value=active_zone_name, 
            key="rename_input",
            on_change=handle_rename_submit
        )
        
        st.divider()
        
        # SECTION B: ADD EXTRA ZONE
        st.subheader("➕ Add Extra Zone")
        st.text_input(
            "Zone Name", 
            key="add_input",
            on_change=handle_add_submit
        )
        if st.session_state.add_input.strip() in profiles and st.session_state.add_input.strip() != "":
            st.error("A zone with that name already exists.")

        st.divider()
        
        # SECTION C: REMOVE ACTIVE ZONE
        st.subheader("🗑️ Remove This Zone")
        st.markdown(f"Wipe profile configuration and structural metrics for **{active_zone_name}**.")
        if st.button(f"Delete {active_zone_name}", type="primary", use_container_width=True):
            try:
                with conn.session as session:
                    session.execute(
                        text("DELETE FROM zone_profiles WHERE property = :prop AND zone_name = :zone AND user_id = :user_id"),
                        {"prop": active_prop, "zone": active_zone_name, "user_id": st.session_state.user_id}
                    )
                    session.execute(
                        text("DELETE FROM watering_logs WHERE property = :prop AND zone_name = :zone AND user_id = :user_id"),
                        {"prop": active_prop, "zone": active_zone_name, "user_id": st.session_state.user_id}
                    )
                    session.commit()
            except Exception:
                pass
                
            if active_zone_name in profiles:
                profiles.pop(active_zone_name)
            
            save_profiles(profiles)
            st.cache_data.clear()
            st.toast(f"💥 Wiped {active_zone_name} from dashboard configuration profile.")
            st.rerun()


# 3.5 Geolocation Cache Utility
@st.cache_data(ttl=3600)
def get_coords_cached(zip_code):
    lat, lon, name = core_logic.get_coords(zip_code)
    return lat, lon

lat, lon = get_coords_cached(active_zip)


# ==============================================================================
# 4. IRRIGATION SPECIFICATIONS (AUTO-SAVING INPUTS)
# ==============================================================================
st.sidebar.header("💧 Irrigation Specs")

current_area = int(current_zone.get("area", 1000))
current_flow = int(current_zone.get("flow", 5))
saved_soil = current_zone.get("soil", "Loam")
current_depth = int(current_zone.get("depth", 12))
current_mad = int(current_zone.get("mad", 50))
current_start_dt = current_zone.get("start_date", str(datetime.now().date()))

# Render UI Controls
area = st.sidebar.number_input("Zone Area (sq ft)", min_value=1, value=current_area, step=1, format="%d")
flow = st.sidebar.number_input("Zone Flow (GPM)", min_value=1, value=current_flow, step=1, format="%d")

soil_types = list(core_logic.SOIL_DATA.keys())
try:
    soil_index = soil_types.index(saved_soil)
except ValueError:
    soil_index = soil_types.index("Loam")
soil_choice = st.sidebar.selectbox("Soil", soil_types, index=soil_index)


depth_in = st.sidebar.slider(
    "Root Depth (in)", 
    min_value=1,   
    max_value=24,  
    value=current_depth, 
    help="The target active root depth profile. Deeper roots have access to a larger structural water reservoir."
)
mad = st.sidebar.slider(
    "Manageable Allowable Depletion (MAD %)",
    min_value=10,
    max_value=80,
    value=current_mad,
    step=5,
    help="The percentage of soil water allowed to dry out before triggering an irrigation cycle."
)

# Auto-Save Engine: Updates automatically if any parameter is altered
if (area != current_area or 
    flow != current_flow or 
    soil_choice != saved_soil or 
    depth_in != current_depth or 
    mad != current_mad):
    
    profiles[active_zone_name] = {
        "zip": active_zip,
        "area": area, 
        "flow": flow, 
        "soil": soil_choice, 
        "depth": depth_in, 
        "mad": mad,
        "start_date": current_start_dt  
    }
    
    if active_zone_name != "Default Zone" and "Default Zone" in profiles:
        del profiles["Default Zone"]
        
    save_profiles(profiles)
    st.rerun()
    
st.sidebar.divider()


# ==============================================================================
# 5. WATER LOGGING MANAGEMENT
# ==============================================================================
st.sidebar.header("📝 Log a Watering Event")

# 5.1 Callback for Runtime Logger
def handle_quick_submit():
    if st.session_state.quick_mins > 0:
        flow_val = float(flow)
        area_val = float(area)

        # Apply precipitation rate formulas
        if area_val > 0:
            rate = (flow_val * 96.25) / area_val
        else:
            rate = 0.5 
        
        calc_inches = (st.session_state.quick_mins / 60.0) * rate
        save_log(active_zone_name, st.session_state.quick_mins, calc_inches)
        
        st.toast(f"🌱 Logged {st.session_state.quick_mins} mins to {active_zone_name} history!")
        st.session_state.quick_mins = 0.0

# 5.2 Render Runtime Input
st.sidebar.number_input(
    label="⏱️ Enter Runtime Minutes & Hit Enter:",
    min_value=0.0,
    max_value=240.0,
    value=0.0,
    step=1.0,
    key="quick_mins",
    on_change=handle_quick_submit
)


# ==============================================================================
# 6. MATH ENGINE (PHYSICAL PROPERTY EXTRACTION)
# ==============================================================================
aw_per_foot, paw_total, ad_limit = core_logic.calculate_irrigation_limits(soil_choice, depth_in, mad)

soil_info = core_logic.SOIL_DATA.get(soil_choice, core_logic.SOIL_DATA["Loam"])
fc_raw = soil_info["FC"]
pwp_raw = soil_info["PWP"]
fc_inft = fc_raw * 12
pwp_inft = pwp_raw * 12
rz_ft = depth_in / 12
aw_capacity_inft = (fc_raw - pwp_raw) * 12



# ==============================================================================
# 7. WEATHER ENGINE (API & DATA SYNCHRONIZATION)
# ==============================================================================

# 7.1 Setup API Sessions
cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


# 7.2 Core Integrated Fetching Routine
@st.cache_data(ttl=3600)
def fetch_weather_integrated(lat, lon, start_date_str):
    start_dt = pd.to_datetime(start_date_str).date()
    today = datetime.now().date()
    days_back = (today - start_dt).days
    
    if days_back > 90:
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": str(start_dt),
            "end_date": str(today),
            "daily": ["et0_fao56", "precipitation"],
            "timezone": "auto",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch"
        }
        is_hourly = False
    else:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": ["et0_fao_evapotranspiration", "precipitation"],
            "timezone": "auto",
            "past_days": 92,
            "forecast_days": 14
        }
        is_hourly = True
        
    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        
        if is_hourly:
            hourly = response.Hourly()
            et_values = hourly.Variables(0).ValuesAsNumpy()
            precip_values = hourly.Variables(1).ValuesAsNumpy()
            
            dates = pd.date_range(
                start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
                end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=hourly.Interval()),
                inclusive="left"
            )
            df_hourly = pd.DataFrame({"time": dates, "et0": et_values, "rain": precip_values})
            df_hourly['time'] = df_hourly['time'].dt.tz_convert(None) 
            
            # Convert metric (mm) to inches and group by day
            df_daily = df_hourly.set_index("time").resample('D').sum().reset_index()
            df_daily['ET0 (in)'] = df_daily['et0'] / 25.4
            df_daily['Rain (in)'] = df_daily['rain'] / 25.4
        else:
            daily = response.Daily()
            et_values = daily.Variables(0).ValuesAsNumpy()
            precip_values = daily.Variables(1).ValuesAsNumpy()
            
            dates = pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left"
            )
            df_daily = pd.DataFrame({"time": dates, "ET0 (in)": et_values, "Rain (in)": precip_values})
            df_daily['time'] = df_daily['time'].dt.tz_convert(None)
            
        df_daily['time'] = df_daily['time'].dt.normalize()
        return df_daily[['time', 'ET0 (in)', 'Rain (in)']]
        
    except Exception as e:
        st.error(f"Weather API Error: {e}")
        return None


# 7.3 Weather Processing and Data Merger
zone_start_date_str = current_zone.get("start_date", str(datetime.now().date() - pd.Timedelta(days=7)))
df_api = fetch_weather_integrated(lat, lon, zone_start_date_str)

if df_api is not None:
    archive_weather(df_api)
    df_permanent = load_weather_history()
    df_daily = pd.concat([df_permanent, df_api]).drop_duplicates(subset='time', keep='last').sort_values('time')
    df_daily['time'] = pd.to_datetime(df_daily['time']).dt.normalize()
    
    # Enforce threshold constraints to retain structural history
    earliest_allowed_date = pd.Timestamp(datetime.now().date()) - pd.Timedelta(days=180)
    df_daily = df_daily[df_daily['time'] >= earliest_allowed_date]
    
    # Merge local zone schedules and database records
    all_logs = load_logs()
    zone_logs = all_logs.get(active_zone_name, [])
    
    if zone_logs:
        log_df = pd.DataFrame(zone_logs)
        log_df['time'] = pd.to_datetime(log_df['date']).dt.normalize()
        log_daily = log_df.groupby('time')['inches'].sum().reset_index()
        log_daily.columns = ['time', 'Irrigation (in)']
        df_daily = pd.merge(df_daily, log_daily, on='time', how='left').fillna(0)
    else:
        df_daily['Irrigation (in)'] = 0.0

    # Separate dataframes into respective operational views
    today_dt = pd.Timestamp(datetime.now().date()).normalize()
    df_history = df_daily[df_daily['time'] < today_dt].copy()
    df_forecast = df_daily[df_daily['time'] >= today_dt].copy()


# ==============================================================================
# 8. DEFICIT CALCULATION (7-DAY ROLLING STARTING WARMUP)
# ==============================================================================
    zone_start_str = current_zone.get("start_date", str(datetime.now().date()))
    zone_start_ts = pd.Timestamp(zone_start_str).normalize()
    
    # Establish previous rolling window to prevent cold start assumptions
    warmup_start_ts = zone_start_ts - pd.Timedelta(days=7)
    mask = (df_daily['time'] >= warmup_start_ts) & (df_daily['time'] <= today_dt)
    zone_weather = df_daily.loc[mask].sort_values('time')
    
    running_deficit = 0.0
    for idx, row in zone_weather.iterrows():
        running_deficit += row['ET0 (in)']
        running_deficit -= (row['Rain (in)'] + row['Irrigation (in)'])
        if running_deficit < 0:
            running_deficit = 0.0
            
    current_deficit = running_deficit
    gallons = current_deficit * area * 0.623
    runtime = gallons / flow if flow > 0 else 0


# ==============================================================================
# 9. MAIN DASHBOARD METRICS
# ==============================================================================
    st.markdown(f"### {active_prop} : {active_zone_name} <span style='color:gray; font-size:0.8em;'>({active_zip})</span>", unsafe_allow_html=True)
    st.divider()
    
    seven_day_et = df_forecast.iloc[0:7]['ET0 (in)'].sum() if len(df_forecast) >= 7 else df_forecast['ET0 (in)'].sum()
    seven_day_rain = df_forecast.iloc[0:7]['Rain (in)'].sum() if len(df_forecast) >= 7 else df_forecast['Rain (in)'].sum()
    
    # Establish system recommendation warning boundaries
    if current_deficit < ad_limit:
        status_msg = "✋ Wait to Water"
    else:
        status_msg = "💧 Time to Water!"
        
    m1, m2, m3, m4 = st.columns(4)
    
    # Column A: Water to Apply
    m1.metric(
        label="Water to Apply", 
        value=f"{current_deficit:.2f}\"",
        help="The current root-zone moisture deficit relative to field capacity."
    )
    
    # Column B: Allowable Depletion
    m2.metric(
        label="Allowable Depletion", 
        value=f"{ad_limit:.2f}\"",
        help="The maximum water allowed to deplete before plant health experiences crop stress."
    )
    
    # Column C: Cumulative ET Forecast
    m3.metric(
        label="7-Day ET Forecast", 
        value=f"{seven_day_et:.2f}\"",
        help="Total atmospheric moisture loss modeled over the next week."
    )
    
    # Column D: Cumulative Rain Forecast
    m4.metric(
        label="7-Day Rain Forecast", 
        value=f"{seven_day_rain:.2f}\"",
        help="Total precipitation expected over the next week."
    )
    
    st.markdown(f"**Status:** {status_msg} | **Estimated Runtime:** {runtime:.1f} min ({gallons:.0f} gal needed)")
    st.divider()
  
    # Immediate Guidance Callouts
    if seven_day_rain > current_deficit and current_deficit > 0:
        st.warning(f"🌧️ **Rain is coming:** The 7-day forecast predicts **{seven_day_rain:.2f}\"** of rain. Consider skipping today!")
    elif current_deficit <= 0:
        st.success(f"✅ **Soil is Hydrated!**")
    else:
        st.info(f"⏱️ **Irrigation Plan:** Run for **{runtime:.1f} minutes** to refill root zone capacity.")


# ==============================================================================
# 10. VISUAL ANALYTICS & TABLES
# ==============================================================================
st.divider()
with st.expander("📈 View Water Balance Graph", expanded=True):
    st.write(f"### 📈 Water Balance for {active_zone_name}")

    if not df_daily.empty:
        now_dt = pd.Timestamp(datetime.now().date()).normalize()
        
        # Configure Default View Zoom Bounds
        view_start = now_dt - pd.Timedelta(days=7)
        view_end = now_dt + pd.Timedelta(days=14)
        lookback_days = 180  
        data_start = now_dt - pd.Timedelta(days=lookback_days)
        df_zoom = df_daily[df_daily['time'] >= data_start].copy()
        
        # Define Unified Interactive X-Axis Scale
        x_axis = alt.X('time:T', 
                       title='Date', 
                       scale=alt.Scale(domain=[view_start, view_end]),
                       axis=alt.Axis(format='%b %d'))

        # Chart Layer A: Evapotranspiration Line
        et_chart = alt.Chart(df_zoom).mark_line(strokeWidth=3, color='#FF8C00').encode(
            x=x_axis,
            y=alt.Y('ET0 (in):Q', title='Inches'),
            tooltip=['time:T', 'ET0 (in):Q']
        )

        # Chart Layer B: Rainfall Bars
        rain_chart = alt.Chart(df_zoom).mark_bar(opacity=0.5, color='#ADD8E6').encode(
            x='time:T',
            y='Rain (in):Q',
            tooltip=['time:T', 'Rain (in):Q']
        )

        # Chart Layer C: Irrigation Events
        irr_chart = alt.Chart(df_zoom).mark_bar(size=10, color='#003366').encode(
            x='time:T',
            y='Irrigation (in):Q',
            tooltip=['time:T', 'Irrigation (in):Q']
        )

        # Chart Layer D: Today Reference Marker
        today_line = alt.Chart(pd.DataFrame({'time': [now_dt]})).mark_rule(
            color='red', strokeDash=[5,5], strokeWidth=2
        ).encode(x='time:T')

        # Combine, render, and freeze scale properties to prevent scrolling issues
        final_chart = alt.layer(rain_chart, irr_chart, et_chart, today_line).properties(
            height=400
        ).interactive(bind_y=False)

        st.altair_chart(final_chart, use_container_width=True)

    # Centered Explanatory Key Legend
    st.markdown("""
    <div style="display: flex; gap: 20px; font-size: 0.8em; justify-content: center; margin-bottom: 20px;">
        <div><span style="color:#FF8C00; font-weight:bold;">━</span> ET (Loss)</div>
        <div><span style="color:#ADD8E6; font-weight:bold;">▇</span> Rain (Gain)</div>
        <div><span style="color:#003366; font-weight:bold;">▇</span> Irrigation (Gain)</div>
        <div><span style="color:red; font-weight:bold;">---</span> Today</div>
    </div>
    """, unsafe_allow_html=True)

# Forecast and Archive Tables View
with st.expander("📋 View Forecast & History Tables", expanded=False):
    tab1, tab2 = st.tabs(["🗓️ Forecast (Next 14 Days)", "📜 History (Past 90 Days)"])
    with tab1:
        st.dataframe(df_forecast.set_index("time").style.format("{:.2f}"), use_container_width=True)
    with tab2:
        st.dataframe(df_history.sort_values('time', ascending=False).set_index("time").style.format("{:.2f}"), use_container_width=True)


# ==============================================================================
# 11. GLOBAL WATER USAGE TRACKER & DATASET LEDGER
# ==============================================================================
st.divider()
st.header("📈 Global Water Usage Tracker")

all_logs = load_logs()

if all_logs:
    combined_data = []
    for zone, events in all_logs.items():
        z_prof = profiles.get(zone, profiles[list(profiles.keys())[0]])
        z_area = z_prof.get("area", 1000)
        z_flow = z_prof.get("flow", 5.0)
        
        for event in events:
            mins = event.get("minutes", 0)
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

        # Render Totals Cards
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Events", len(usage_df))
        col2.metric("Total Volume", f"{total_gal:,.0f} gal", help="Cumulative volume used across all zones.")
        col3.metric("Total Depth", f"{total_inches:.2f}\"", help="Cumulative irrigation depth applied.")
        col4.metric("Total Run Time", f"{total_mins:,.0f} min")

        # Core Ledger Interactive Editor Setup
        with st.expander("📂 View & Edit Full Irrigation Ledger", expanded=True):
            st.caption("Editing **Minutes** or **Zone** will automatically recalculate **Inches** based on Zone parameters.")
            
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

            # Execution Engine for Interactive Ledger Modifications
            if not usage_df.equals(edited_df):
                new_logs = {}
                for _, row in edited_df.iterrows():
                    if pd.isna(row["Date"]) or pd.isna(row["Zone"]):
                        continue
                    z_name = row["Zone"]
                    mins = row["Minutes"]
                    z_prof = profiles.get(z_name, profiles[list(profiles.keys())[0]])
                    z_flow = z_prof.get("flow", 5.0)
                    z_area = z_prof.get("area", 1000)
                    
                    calc_inches = (mins * z_flow) / (z_area * 0.623)
                    
                    if z_name not in new_logs:
                        new_logs[z_name] = []
                    
                    date_val = row["Date"]
                    date_str = str(date_val.date()) if hasattr(date_val, "date") else str(date_val)
                    
                    new_logs[z_name].append({
                        "date": date_str,
                        "minutes": mins,
                        "inches": calc_inches
                    })
                    
                if new_logs:
                    data_manager.save_json(LOG_FILE, new_logs)
                    st.success("Global logs updated and water depths recalculated!")
                    st.rerun()


            # Global Volume Comparison chart
            st.write("### 📊 Gallons Used per Zone")
            zone_usage_gal = edited_df.groupby("Zone")["Gallons"].sum()
            st.bar_chart(zone_usage_gal)
    else:
        st.info("No data found in logs.")
else:
    st.info("No watering events logged yet. Use the sidebar to log your first event!")


# ==============================================================================
# 12. SCIENCE REFERENCE LIBRARY
# ==============================================================================
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
        st.write(f"Available Water (AW): {aw_per_foot:.2f} in/ft") 
    with col_b:
        st.write("**Root Zone & Depletion**")
        st.write(f"Root Zone (RZ): {rz_ft:.2f} ft ({depth_in} in)")
        st.write(f"Plant Available Water (PAW): {paw_total:.2f} inches") 
        st.write(f"Allowable Depletion (AD): {ad_limit:.2f} inches") 
        
    st.divider()
    depletion_status = (current_deficit / ad_limit) * 100 if ad_limit > 0 else 0
    st.info(f"**Current Status:** Your deficit is {current_deficit:.2f}\". This represents {depletion_status:.1f}% of your Allowable Depletion limit.")

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
    The water loss value is calculated using reference **ET₀** parameters mapped back to the standard **FAO-56 Penman-Monteith Equation**.
    
    **Environmental Inputs Tracked:**
    1. **Solar Radiation:** Primary thermodynamic energy engine.
    2. **Ambient Temp:** Temperature gradients driving pressure.
    3. **Relative Humidity:** Dry atmospheric vapor pressure gradients.
    4. **Wind Speed:** Boundary layer transport factors.
    """)
    st.info("Data Source: High-resolution Open-Meteo meteorological API.")
    
with tab_refs:
    st.write("### References")
    st.markdown("""
    **Reference A: Estimating Soil Physics Limits**
    * Saxton, Keith S., and Walter J. Rawls. 'Estimating Soil Water Characteristics from Texture, Organic Matter, and Salinity.' *Soil Science Society of America Journal*, vol. 70, no. 5, 2006, pp. 1569-1578.
    
    **Reference B: Reference Crop ET Evaluations**
    * Allen, Richard G., et al. 'Crop Evapotranspiration: Guidelines for Computing Crop Water Requirements.' *FAO Irrigation and Drainage Paper 56*, 1998.
    """)


# ==============================================================================
# 13. SYSTEM SECURITY & BACKUP UTILITIES
# ==============================================================================
st.divider()
with st.expander("🛡️ Data Security & Backups"):
    st.write("### 📥 Download App Data")
    st.caption("Export active profiles and logging archives safely to disk.")
    
    profiles_string = json.dumps(profiles, indent=4)
    all_logs = load_logs()
    logs_string = json.dumps(all_logs, indent=4)
    
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button(
            label="📥 Download Zone Profiles",
            data=profiles_string,
            file_name=f"{active_prop}_profiles.json",
            mime="application/json",
            use_container_width=True
        )
    with dl_col2:
        st.download_button(
            label="📥 Download Watering Logs",
            data=logs_string,
            file_name=f"{active_prop}_history_log.json",
            mime="application/json",
            use_container_width=True
        )

    st.divider()
    st.write("### 🚀 Server System Backups")
    st.write("Click below to build a timestamped configuration clone of directory assets.")
    
    if st.button("🚀 Create Instant Backup"):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        current_backup_path = os.path.join(BACKUP_DIR, f"Backup_{timestamp}")
        
        try:
            shutil.copytree(DATA_DIR, current_backup_path)
            st.success(f"Backup Successful! Archive stored in local path: `{current_backup_path}`")
            
            # Show historical catalog
            st.write("### Recent Backups on Disk:")
            all_backups = sorted(os.listdir(BACKUP_DIR), reverse=True)
            for b in all_backups[:5]: 
                st.text(f"📁 {b}")
                
        except Exception as e:
            st.error(f"Backup failed: {e}")

    st.caption("Note: Local platform checks protect standard container services. Secure physical directories systematically.")


# Footer Branding
st.divider()
st.markdown(
    """
    <div style='text-align: center; color: gray; font-size: 0.8em;'>
        Irrigation Dashboard • v0.34
    </div>
    """, 
    unsafe_allow_html=True
)