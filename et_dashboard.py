import os
import json
import requests
import requests_cache
import platform
import shutil
from datetime import datetime, timedelta
import pandas as pd
import altair as alt
import streamlit as st
from streamlit_option_menu import option_menu
from sqlalchemy import text
from retry_requests import retry
import openmeteo_requests

# Import local business modules
import core_logic
import data_manager

# ==============================================================================
# 0. CONSTANTS, INITIALIZATION & HELPER FUNCTIONS
# ==============================================================================

# Optional local-only Supabase configuration. This keeps the app usable even when
# the user has not yet configured shared cloud storage.
SUPABASE_CONFIG = {}
try:
    supabase_block = st.secrets.get("supabase", {})
    if isinstance(supabase_block, dict):
        SUPABASE_CONFIG = {
            "url": supabase_block.get("url") or os.getenv("SUPABASE_URL"),
            "anon_key": supabase_block.get("anon_key") or os.getenv("SUPABASE_ANON_KEY"),
            "service_role_key": supabase_block.get("service_role_key") or os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        }
except Exception:
    SUPABASE_CONFIG = {}

SUPABASE_READY = bool(SUPABASE_CONFIG.get("url") and SUPABASE_CONFIG.get("anon_key"))

# Safe local fallback Mock if database credentials are not present in secrets.toml
class MockConnection:
    def query(self, query, params=None, ttl=0):
        # Return empty DataFrame if DB isn't configured so local JSON can take over
        return pd.DataFrame(columns=['zone_name', 'log_date', 'minutes', 'inches'])
    class MockSession:
        def execute(self, *args, **kwargs): pass
        def commit(self): pass
    session = MockSession()

# 0.1 Setup Database Connection (Fallback to Streamlit SQL Connection)

# Schema creation/migration used to run as plain top-level code, which meant all 10
# CREATE/ALTER statements below fired on every single rerun -- i.e. every click, every
# widget interaction -- not just once. @st.cache_resource runs this exactly once per server
# process (shared across every session on that process) and skips it on every rerun after.
@st.cache_resource
def _init_schema(_conn):
    with _conn.session as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS app_users (
                username VARCHAR(50) PRIMARY KEY,
                password_hash VARCHAR(256) NOT NULL
            );
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS properties (
                user_id VARCHAR(100) NOT NULL,
                property_name VARCHAR(100) NOT NULL,
                zip_code VARCHAR(20) NOT NULL,
                PRIMARY KEY (user_id, property_name)
            );
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS zones (
                user_id VARCHAR(100) NOT NULL,
                property_name VARCHAR(100) NOT NULL,
                zone_name VARCHAR(100) NOT NULL,
                area INTEGER NOT NULL DEFAULT 1000,
                flow NUMERIC NOT NULL DEFAULT 5,
                soil VARCHAR(50) NOT NULL DEFAULT 'Loam',
                depth INTEGER NOT NULL DEFAULT 12,
                mad INTEGER NOT NULL DEFAULT 50,
                start_date DATE NOT NULL DEFAULT CURRENT_DATE,
                PRIMARY KEY (user_id, property_name, zone_name)
            );
        """))
        # Migration for zones tables created before the plant/Kc column existed --
        # CREATE TABLE IF NOT EXISTS above is a no-op against the already-live production table.
        session.execute(text("""
            ALTER TABLE zones ADD COLUMN IF NOT EXISTS plant VARCHAR(80) NOT NULL DEFAULT 'Cool-Season Turf (Bluegrass, Fescue, Ryegrass)';
        """))
        # Migration for the Easy/Advanced irrigation-spec mode -- head_type only matters
        # in Easy Mode (drives the flow estimate), but is stored for every zone either way.
        session.execute(text("""
            ALTER TABLE zones ADD COLUMN IF NOT EXISTS head_type VARCHAR(20) NOT NULL DEFAULT 'Spray';
        """))
        # unit_count is the homeowner-countable quantity Easy Mode derives area/flow from
        # (head count for Spray/Rotor, plant count for Drip); plant_size only applies to Drip.
        session.execute(text("""
            ALTER TABLE zones ADD COLUMN IF NOT EXISTS unit_count INTEGER NOT NULL DEFAULT 4;
        """))
        session.execute(text("""
            ALTER TABLE zones ADD COLUMN IF NOT EXISTS plant_size VARCHAR(40) NOT NULL DEFAULT 'Medium (typical shrub)';
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS watering_logs (
                user_id VARCHAR(100) NOT NULL,
                property_name VARCHAR(100) NOT NULL,
                zone_name VARCHAR(100) NOT NULL,
                log_date DATE NOT NULL,
                minutes NUMERIC NOT NULL,
                inches NUMERIC NOT NULL
            );
        """))
        # Shared weather cache: one row per location/day, reused across every user and
        # zone at that location so a single Open-Meteo fetch serves everyone, and so the
        # archive survives Streamlit Cloud container reboots (unlike the local JSON/
        # in-memory caches, which are wiped on every reboot).
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS weather_cache (
                lat NUMERIC NOT NULL,
                lon NUMERIC NOT NULL,
                log_date DATE NOT NULL,
                et0_in NUMERIC NOT NULL,
                rain_in NUMERIC NOT NULL,
                PRIMARY KEY (lat, lon, log_date)
            );
        """))
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS weather_fetch_meta (
                lat NUMERIC NOT NULL,
                lon NUMERIC NOT NULL,
                last_fetch_utc TIMESTAMP NOT NULL,
                PRIMARY KEY (lat, lon)
            );
        """))
        session.commit()

DB_CONNECTION_ERROR = None
try:
    conn = st.connection("postgresql", type="sql")
    try:
        _init_schema(conn)
    except Exception as e:
        DB_CONNECTION_ERROR = f"Connected, but table setup failed: {e}"
except Exception as e:
    conn = MockConnection()
    DB_CONNECTION_ERROR = str(e)

# Visible, trustworthy connection status (previously the only indicator in the app checked
# whether Supabase REST secrets were present, not whether the Postgres connection actually
# worked -- so it could read "connected" while silently running on the local JSON fallback).
if isinstance(conn, MockConnection):
    st.sidebar.caption(f"\U0001F4BE Local-only mode (DB not connected){': ' + DB_CONNECTION_ERROR if DB_CONNECTION_ERROR else ''}")
else:
    st.sidebar.caption("☁️ Cloud sync active")

# 0.15 Session-Scoped Read Cache
# Streamlit reruns this entire script top-to-bottom on every widget interaction (every
# button click, slider drag, selectbox change). Any DB read sitting at module level --
# properties, zone profiles, watering logs, weather history -- was firing again on every
# single rerun, each one a real network round trip to the Supabase pooler, sequentially,
# before the page could even render. These helpers cache read results in st.session_state
# instead, so each piece of data is fetched once per session and reused on every later
# rerun; call sites that write update or invalidate the matching cache key directly rather
# than relying on a short query ttl to eventually expire.
def _cache_get(key, loader):
    if key not in st.session_state:
        st.session_state[key] = loader()
    return st.session_state[key]


def _cache_invalidate(key):
    st.session_state.pop(key, None)


# 0.2 User DB Authentication Helpers
def db_register_user(username, password):
    username_lower = username.strip().lower()
    hashed = data_manager.hash_password(password)
    # Check if database is mock or real
    if not isinstance(conn, MockConnection) and hasattr(conn, "session"):
        try:
            with conn.session as session:
                res = session.execute(
                    text("SELECT 1 FROM app_users WHERE username = :user"),
                    {"user": username_lower}
                ).fetchone()
                if res:
                    return False
                session.execute(
                    text("INSERT INTO app_users (username, password_hash) VALUES (:user, :hash)"),
                    {"user": username_lower, "hash": hashed}
                )
                session.commit()
                return True
        except Exception:
            pass
    # Fallback to local files
    return data_manager.register_user_local(username, password)

def db_authenticate_user(username, password):
    username_lower = username.strip().lower()
    if not isinstance(conn, MockConnection) and hasattr(conn, "session"):
        try:
            with conn.session as session:
                res = session.execute(
                    text("SELECT password_hash FROM app_users WHERE username = :user"),
                    {"user": username_lower}
                ).fetchone()
                if not res:
                    return False
                stored_hash = res[0]
                return data_manager.verify_password(password, stored_hash)
        except Exception:
            pass
    # Fallback to local files
    return data_manager.authenticate_user_local(username, password)

# ==============================================================================
# USER LOGIN / REGISTRATION FLOW
# ==============================================================================

def load_properties_from_cloud():
    """Loads a user's property list from the shared Postgres/Supabase cloud table if available."""
    if isinstance(conn, MockConnection):
        return None
    try:
        df = conn.query(
            "SELECT property_name, zip_code FROM properties WHERE user_id = :user_id",
            params={"user_id": st.session_state.user_id},
            ttl=5,
        )
        if not df.empty:
            return df.set_index("property_name")["zip_code"].to_dict()
    except Exception:
        return None
    return None


def save_property_to_cloud(prop_name, zip_code):
    """Stores a user's property in the shared cloud table when the SQL connection is available."""
    if isinstance(conn, MockConnection):
        return
    try:
        with conn.session as session:
            session.execute(text("""
                INSERT INTO properties (user_id, property_name, zip_code)
                VALUES (:user_id, :prop_name, :zip_code)
                ON CONFLICT (user_id, property_name)
                DO UPDATE SET zip_code = EXCLUDED.zip_code;
            """), {
                "user_id": st.session_state.user_id,
                "prop_name": prop_name,
                "zip_code": zip_code,
            })
            session.commit()
    except Exception:
        pass


def load_profiles_from_cloud(active_property):
    """Loads a user's zone profiles from the shared Postgres/Supabase cloud table if available."""
    if isinstance(conn, MockConnection):
        return None
    try:
        df = conn.query(
            """
                SELECT zone_name, area, flow, soil, depth, mad, start_date
                FROM zones
                WHERE user_id = :user_id AND property_name = :property_name
            """,
            params={"user_id": st.session_state.user_id, "property_name": active_property},
            ttl=0,
        )
        if not df.empty:
            zone_dict = {}
            for _, row in df.iterrows():
                zone_dict[row["zone_name"]] = {
                    "zip": active_zip,
                    "area": int(row["area"]),
                    "flow": float(row["flow"]),
                    "soil": row["soil"],
                    "depth": int(row["depth"]),
                    "mad": int(row["mad"]),
                    "start_date": str(row["start_date"]),
                }
            return zone_dict
    except Exception:
        return None
    return None


def save_profiles_to_cloud(profiles_dict, active_property):
    """Stores the current zone profile set under the shared cloud table, scoped to the user and property."""
    if isinstance(conn, MockConnection):
        return
    try:
        with conn.session as session:
            for zone_name, config in profiles_dict.items():
                session.execute(text("""
                    INSERT INTO zones (
                        user_id, property_name, zone_name, area, flow, soil, depth, mad, start_date
                    )
                    VALUES (
                        :user_id, :property_name, :zone_name, :area, :flow, :soil, :depth, :mad, :start_date
                    )
                    ON CONFLICT (user_id, property_name, zone_name)
                    DO UPDATE SET
                        area = EXCLUDED.area,
                        flow = EXCLUDED.flow,
                        soil = EXCLUDED.soil,
                        depth = EXCLUDED.depth,
                        mad = EXCLUDED.mad,
                        start_date = EXCLUDED.start_date;
                """), {
                    "user_id": st.session_state.user_id,
                    "property_name": active_property,
                    "zone_name": zone_name,
                    "area": int(config.get("area", 1000)),
                    "flow": float(config.get("flow", 5)),
                    "soil": config.get("soil", "Loam"),
                    "depth": int(config.get("depth", 12)),
                    "mad": int(config.get("mad", 50)),
                    "start_date": config.get("start_date", str(datetime.now().date())),
                })
            session.commit()
    except Exception:
        pass


def load_logs_from_cloud(active_property):
    """Loads watering history for the current user/property from the shared cloud table when available."""
    if isinstance(conn, MockConnection):
        return None
    try:
        df = conn.query(
            """
                SELECT zone_name, log_date, minutes, inches
                FROM watering_logs
                WHERE user_id = :user_id AND property_name = :property_name
            """,
            params={"user_id": st.session_state.user_id, "property_name": active_property},
            ttl=5,
        )
        if not df.empty:
            return df
    except Exception:
        return None
    return None


def save_log_to_cloud(zone_name, minutes, inches_applied, active_property):
    """Mirrors a watering event into the shared cloud table when the SQL connection is available."""
    if isinstance(conn, MockConnection):
        return
    try:
        with conn.session as session:
            session.execute(text("""
                INSERT INTO watering_logs (user_id, property_name, zone_name, log_date, minutes, inches)
                VALUES (:user_id, :property_name, :zone_name, :log_date, :minutes, :inches);
            """), {
                "user_id": st.session_state.user_id,
                "property_name": active_property,
                "zone_name": zone_name,
                "log_date": datetime.now().date(),
                "minutes": float(minutes),
                "inches": float(inches_applied),
            })
            session.commit()
    except Exception:
        pass


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🌱 Irrigation Dashboard Login")
    st.markdown("Welcome! Please log in or sign up to manage your properties and zone configurations.")

    tab_login, tab_signup = st.tabs(["🔒 Login", "📝 Sign Up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username", key="login_username").strip()
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In", use_container_width=True)
            if submitted:
                if username and password:
                    if db_authenticate_user(username, password):
                        st.session_state.authenticated = True
                        st.session_state.username = username
                        st.session_state.user_id = username
                        st.success(f"Welcome back, {username}!")
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
                else:
                    st.warning("Please fill in all fields.")

    with tab_signup:
        with st.form("signup_form"):
            new_username = st.text_input("Choose Username", key="signup_username").strip()
            new_password = st.text_input("Choose Password", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm Password", type="password", key="signup_confirm_password")
            submitted = st.form_submit_button("Create Account", use_container_width=True)
            if submitted:
                if new_username and new_password:
                    if len(new_username) < 3:
                        st.error("Username must be at least 3 characters.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    elif len(new_password) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        if db_register_user(new_username, new_password):
                            st.success("Account created successfully! You can now log in.")
                        else:
                            st.error("Username is already taken.")
                else:
                    st.warning("Please fill in all fields.")
    st.stop()

# ==============================================================================
# TOP NAVIGATION HEADER (section tabs + profile menu)
# ==============================================================================
nav_col, profile_col = st.columns([6, 1])

with nav_col:
    selected_tab = option_menu(
        menu_title=None,
        options=["Dashboard", "Properties", "Ledger", "Reference"],
        icons=["speedometer2", "houses", "table", "book"],
        menu_icon="cast",
        default_index=0,
        orientation="horizontal",
    )

with profile_col:
    st.write("")
    with st.popover(f"👤 {st.session_state.username}"):
        st.markdown(f"**Logged in as:** {st.session_state.username}")
        mode_label = "💾 Local-only" if isinstance(conn, MockConnection) else "☁️ Cloud sync"
        st.caption(f"Connection: {mode_label}")
        st.divider()
        if st.button("🚪 Log Out", use_container_width=True, key="profile_logout_btn"):
            st.session_state.authenticated = False
            st.session_state.username = None
            st.session_state.user_id = "default_user"
            st.session_state.active_prop = None
            st.rerun()

# ==============================================================================
# PROPERTY CONFIGURATION & SESSION STATE (Authenticated User)
# ==============================================================================
user_paths = data_manager.get_user_paths(st.session_state.user_id)
PROP_LIST_FILE = user_paths["prop_list"]

properties_dict = _cache_get(
    f"cache_properties::{st.session_state.user_id}",
    lambda: load_properties_from_cloud() or data_manager.load_json(PROP_LIST_FILE, {}),
)

# Render Property Selector in Sidebar
st.sidebar.header("🏠 Active Property")
prop_options = list(properties_dict.keys())

# If the user has no properties, force them to create one
if not prop_options:
    st.sidebar.info("👋 Welcome! Create your first property to get started.")
    new_prop_name = st.sidebar.text_input("Property Name (e.g., Home)", key="first_prop_name")
    new_prop_zip = st.sidebar.text_input("Zip Code", key="first_prop_zip")
    if st.sidebar.button("Create Property", use_container_width=True):
        if new_prop_name.strip() and new_prop_zip.strip():
            p_name = new_prop_name.strip()
            p_zip = new_prop_zip.strip()
            properties_dict[p_name] = p_zip
            data_manager.save_json(PROP_LIST_FILE, properties_dict)
            save_property_to_cloud(p_name, p_zip)
            st.session_state.active_prop = p_name
            st.toast(f"🏡 Property '{p_name}' created!")
            st.rerun()
        else:
            st.sidebar.error("Please enter a valid name and zip code.")
    st.stop()

if "active_prop" not in st.session_state or st.session_state.active_prop not in prop_options:
    st.session_state.active_prop = prop_options[0]

active_prop = st.sidebar.selectbox("Select Active Property", prop_options, index=prop_options.index(st.session_state.active_prop))
st.session_state.active_prop = active_prop
active_zip = properties_dict[active_prop]

# ==============================================================================
# PROPERTIES TAB CONTENT (list + add new property)
# ==============================================================================
if selected_tab == "Properties":
    st.header("🏡 Your Properties")
    st.caption("Manage the properties on your account. Pick which one is active from the sidebar.")

    for prop_name, prop_zip_code in list(properties_dict.items()):
        row_name, row_zip, row_active, row_delete = st.columns([3, 2, 1, 1])
        row_name.write(f"**{prop_name}**")
        row_zip.write(prop_zip_code)
        row_active.write("✅" if prop_name == active_prop else "")
        if row_delete.button("🗑️ Delete", key=f"delete_prop_{prop_name}", use_container_width=True):
            try:
                with conn.session as session:
                    session.execute(
                        text("DELETE FROM zones WHERE property_name = :prop AND user_id = :user_id"),
                        {"prop": prop_name, "user_id": st.session_state.user_id}
                    )
                    session.execute(
                        text("DELETE FROM watering_logs WHERE property_name = :prop AND user_id = :user_id"),
                        {"prop": prop_name, "user_id": st.session_state.user_id}
                    )
                    session.execute(
                        text("DELETE FROM properties WHERE property_name = :prop AND user_id = :user_id"),
                        {"prop": prop_name, "user_id": st.session_state.user_id}
                    )
                    session.commit()
            except Exception:
                pass

            properties_dict.pop(prop_name, None)
            data_manager.save_json(PROP_LIST_FILE, properties_dict)

            # Wipe the property's local zone/log/weather JSON files too, mirroring how zone
            # deletion wipes that zone's profile entry rather than leaving orphaned files behind.
            deleted_paths = data_manager.get_prop_paths_for_user(st.session_state.user_id, prop_name)
            for stale_file in (deleted_paths["db"], deleted_paths["log"], deleted_paths["weather"]):
                if os.path.exists(stale_file):
                    os.remove(stale_file)

            if st.session_state.active_prop == prop_name:
                st.session_state.active_prop = None

            st.toast(f"🗑️ Deleted property '{prop_name}' and its zones/history.")
            st.rerun()

    st.divider()
    st.subheader("➕ Add New Property")
    with st.form("add_property_form", clear_on_submit=True):
        new_prop_name = st.text_input("Property Name", key="new_prop_name_input")
        new_prop_zip = st.text_input("Zip Code", key="new_prop_zip_input")
        submitted = st.form_submit_button("Save Property", use_container_width=True)
        if submitted:
            if new_prop_name.strip() and new_prop_zip.strip():
                p_name = new_prop_name.strip()
                p_zip = new_prop_zip.strip()
                if p_name not in properties_dict:
                    properties_dict[p_name] = p_zip
                    data_manager.save_json(PROP_LIST_FILE, properties_dict)
                    save_property_to_cloud(p_name, p_zip)
                    st.session_state.active_prop = p_name
                    st.toast(f"🏡 Property '{p_name}' added!")
                    st.rerun()
                else:
                    st.error("A property with that name already exists.")
            else:
                st.error("Please enter a valid name and zip code.")

# Dynamic path resolution based on active property and user
paths = data_manager.get_prop_paths_for_user(st.session_state.user_id, active_prop)
PROFILE_FILE = paths["db"]
LOG_FILE = paths["log"]
WEATHER_LOG = paths["weather"]
DATA_DIR = data_manager.DATA_DIR
BACKUP_DIR = data_manager.BACKUP_DIR


def load_profiles_from_supabase():
    """Loads zone profiles from the verified live Supabase/Postgres SQL connection if available."""
    if isinstance(conn, MockConnection) or not hasattr(conn, "query"):
        return None
    if not active_prop:
        return None
    try:
        df = conn.query(
            """
                SELECT zone_name, area, flow, soil, plant, head_type, unit_count, plant_size, depth, mad, start_date
                FROM zones
                WHERE user_id = :user_id
                  AND property_name = :property_name
            """,
            params={"user_id": st.session_state.user_id, "property_name": active_prop},
            ttl=5,
        )
        if df.empty:
            return None

        profiles_from_cloud = {}
        for _, row in df.iterrows():
            zone_name = row.get("zone_name")
            if zone_name:
                profiles_from_cloud[zone_name] = {
                    "zip": active_zip,
                    "area": int(row.get("area", 1000)),
                    "flow": float(row.get("flow", 5)),
                    "soil": row.get("soil", "Loam"),
                    "plant": row.get("plant", "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)"),
                    "head_type": row.get("head_type", "Spray"),
                    "unit_count": int(row.get("unit_count", 4)),
                    "plant_size": row.get("plant_size", "Medium (typical shrub)"),
                    "depth": int(row.get("depth", 12)),
                    "mad": int(row.get("mad", 50)),
                    "start_date": str(row.get("start_date", str(datetime.now().date()))),
                }
        return profiles_from_cloud if profiles_from_cloud else None
    except Exception:
        return None


def save_profiles_to_supabase(profiles_dict):
    """Best-effort mirror of zone profiles to the live SQL connection used by Supabase."""
    if isinstance(conn, MockConnection) or not hasattr(conn, "session"):
        return
    try:
        with conn.session as session:
            for zone_name, config in profiles_dict.items():
                session.execute(text("""
                    INSERT INTO zones (
                        user_id, property_name, zone_name, area, flow, soil, plant, head_type, unit_count, plant_size, depth, mad, start_date
                    )
                    VALUES (
                        :user_id, :property_name, :zone_name, :area, :flow, :soil, :plant, :head_type, :unit_count, :plant_size, :depth, :mad, :start_date
                    )
                    ON CONFLICT (user_id, property_name, zone_name)
                    DO UPDATE SET
                        area = EXCLUDED.area,
                        flow = EXCLUDED.flow,
                        soil = EXCLUDED.soil,
                        plant = EXCLUDED.plant,
                        head_type = EXCLUDED.head_type,
                        unit_count = EXCLUDED.unit_count,
                        plant_size = EXCLUDED.plant_size,
                        depth = EXCLUDED.depth,
                        mad = EXCLUDED.mad,
                        start_date = EXCLUDED.start_date;
                """), {
                    "user_id": st.session_state.user_id,
                    "property_name": active_prop,
                    "zone_name": zone_name,
                    "area": int(config.get("area", 1000)),
                    "flow": float(config.get("flow", 5)),
                    "soil": config.get("soil", "Loam"),
                    "plant": config.get("plant", "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)"),
                    "head_type": config.get("head_type", "Spray"),
                    "unit_count": int(config.get("unit_count", 4)),
                    "plant_size": config.get("plant_size", "Medium (typical shrub)"),
                    "depth": int(config.get("depth", 12)),
                    "mad": int(config.get("mad", 50)),
                    "start_date": config.get("start_date", str(datetime.now().date())),
                })
            session.commit()
    except Exception:
        pass


# 0.4 Helper: Local Zone Profiles I/O using data_manager
def load_profiles():
    """Loads configured zone settings from Supabase when available, otherwise local JSON fallback."""
    default_profile = {
        "Front Lawn": {
            "zip": active_zip,
            "area": 1200,
            "flow": 8.0,
            "soil": "Loam",
            "plant": "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)",
            "head_type": "Spray",
            "unit_count": 4,
            "plant_size": "Medium (typical shrub)",
            "depth": 6,
            "mad": 50,
            "start_date": str(datetime.now().date() - pd.Timedelta(days=7))
        }
    }
    cloud_profiles = load_profiles_from_supabase()
    if cloud_profiles is not None:
        return cloud_profiles
    return data_manager.load_json(PROFILE_FILE, default_profile)

def save_profiles(profiles_dict):
    """Saves structural zone configurations to local JSON and mirrors to Supabase if configured."""
    data_manager.save_json(PROFILE_FILE, profiles_dict)
    save_profiles_to_supabase(profiles_dict)


# ==============================================================================
# 1. DATABASE & STORAGE UTILITIES
# ==============================================================================

# 1.1 Load Zone Logs from Database
def load_logs():
    """Pulls watering history matching this property and active user from shared cloud storage when available."""
    cloud_df = load_logs_from_cloud(active_prop)
    if cloud_df is not None and not cloud_df.empty:
        df = cloud_df
    else:
        query = """
            SELECT zone_name, log_date, minutes, inches 
            FROM watering_logs 
            WHERE property_name = :prop AND user_id = :user_id
        """
        try:
            df = conn.query(query, params={"prop": active_prop, "user_id": st.session_state.user_id}, ttl=5)
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


def _logs_cache_key():
    return f"cache_logs::{st.session_state.user_id}::{active_prop}"


def get_logs_cached():
    """Session-cached watering logs for the active property. load_logs() hits the DB, and
    was previously called from three separate places (Dashboard, Ledger, backup export) --
    each one a fresh network round trip even within the same rerun."""
    return _cache_get(_logs_cache_key(), load_logs)


def invalidate_logs_cache():
    _cache_invalidate(_logs_cache_key())


# 1.2 Save Local Log to Database & JSON Backup
def save_log(zone, minutes, inches_applied):
    """Inserts a verified watering event into the shared cloud table when available and mirrors to local backup."""
    # Step A: Push to SQL Connection if online
    try:
        with conn.session as session:
            query = text("""
                INSERT INTO watering_logs (user_id, property_name, zone_name, log_date, minutes, inches)
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

    save_log_to_cloud(zone, minutes, inches_applied, active_prop)

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

    invalidate_logs_cache()


# 1.2b Shared Cloud Weather Cache (rounded lat/lon so every user/zone at the same
# location reuses one fetch, and history survives Streamlit Cloud container reboots)
def _weather_coord_key(lat, lon):
    return round(float(lat), 2), round(float(lon), 2)


def load_weather_cache_from_cloud(lat, lon):
    if isinstance(conn, MockConnection):
        return None
    coord_lat, coord_lon = _weather_coord_key(lat, lon)
    try:
        df = conn.query(
            """
                SELECT log_date AS time, et0_in AS "ET0 (in)", rain_in AS "Rain (in)"
                FROM weather_cache WHERE lat = :lat AND lon = :lon
            """,
            params={"lat": coord_lat, "lon": coord_lon},
            ttl=300,  # weather_cache only changes via the ~20hr fetch gate below, so this can cache far longer than the other queries
        )
        return df
    except Exception:
        return None


def save_weather_cache_to_cloud(lat, lon, df_daily):
    if isinstance(conn, MockConnection):
        return
    coord_lat, coord_lon = _weather_coord_key(lat, lon)
    try:
        with conn.session as session:
            # Store forecast rows too (not just history) -- the reboot-proof fetch gate can
            # skip real fetches for up to 20h, so the archive needs to double as the source
            # for the forward-looking forecast view during that window. Forecast values get
            # naturally overwritten with actuals once a later real fetch covers that date.
            for _, row in df_daily.iterrows():
                session.execute(text("""
                    INSERT INTO weather_cache (lat, lon, log_date, et0_in, rain_in)
                    VALUES (:lat, :lon, :log_date, :et0, :rain)
                    ON CONFLICT (lat, lon, log_date)
                    DO UPDATE SET et0_in = EXCLUDED.et0_in, rain_in = EXCLUDED.rain_in;
                """), {
                    "lat": coord_lat, "lon": coord_lon,
                    "log_date": row['time'].date(),
                    "et0": float(row["ET0 (in)"]), "rain": float(row["Rain (in)"]),
                })
            session.commit()
    except Exception:
        pass


def get_weather_last_fetch(lat, lon):
    """Returns the UTC datetime weather was last actually fetched from Open-Meteo for this
    location (cloud table if connected, else the local JSON metadata key), or None."""
    if not isinstance(conn, MockConnection):
        coord_lat, coord_lon = _weather_coord_key(lat, lon)
        try:
            df = conn.query(
                "SELECT last_fetch_utc FROM weather_fetch_meta WHERE lat = :lat AND lon = :lon",
                params={"lat": coord_lat, "lon": coord_lon},
                ttl=300,  # only changes via the ~20hr fetch gate this feeds into
            )
            if not df.empty:
                return pd.to_datetime(df.iloc[0]["last_fetch_utc"]).to_pydatetime().replace(tzinfo=None)
        except Exception:
            pass
    meta = data_manager.load_json(WEATHER_LOG, {})
    ts = meta.get("__last_fetch_utc")
    return pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None) if ts else None


def mark_weather_fetched_cloud(lat, lon, fetched_at):
    if isinstance(conn, MockConnection):
        return
    coord_lat, coord_lon = _weather_coord_key(lat, lon)
    try:
        with conn.session as session:
            session.execute(text("""
                INSERT INTO weather_fetch_meta (lat, lon, last_fetch_utc)
                VALUES (:lat, :lon, :ts)
                ON CONFLICT (lat, lon) DO UPDATE SET last_fetch_utc = EXCLUDED.last_fetch_utc;
            """), {"lat": coord_lat, "lon": coord_lon, "ts": fetched_at})
            session.commit()
    except Exception:
        pass


# 1.3 Archive Weather to Local Storage Cache
def archive_weather(df_daily, lat, lon):
    """Stores historical daily ET and Rainfall records locally and (when connected) in the
    shared cloud weather cache, and stamps the last-fetch time so the durable reboot-proof
    gate at the call site knows not to re-fetch too soon."""
    history = data_manager.load_json(WEATHER_LOG, {})
    for _, row in df_daily.iterrows():
        date_str = row['time'].strftime('%Y-%m-%d')
        history[date_str] = {"ET0 (in)": row["ET0 (in)"], "Rain (in)": row["Rain (in)"]}
    fetched_at = datetime.utcnow()
    history["__last_fetch_utc"] = fetched_at.isoformat()
    data_manager.save_json(WEATHER_LOG, history)
    save_weather_cache_to_cloud(lat, lon, df_daily)
    mark_weather_fetched_cloud(lat, lon, fetched_at)


# 1.4 Load Archived Weather History
def load_weather_history(lat, lon):
    """Loads archived weather history, preferring the shared cloud cache (durable across
    Streamlit Cloud reboots) and falling back to the local JSON archive."""
    cloud_df = load_weather_cache_from_cloud(lat, lon)
    if cloud_df is not None and not cloud_df.empty:
        cloud_df['time'] = pd.to_datetime(cloud_df['time'])
        return cloud_df
    data = data_manager.load_json(WEATHER_LOG, {})
    if data:
        # Exclude reserved metadata keys (e.g. rate-limit timestamp)
        weather_data = {k: v for k, v in data.items() if not k.startswith("__")}
        if not weather_data:
            return pd.DataFrame(columns=['time', 'ET0 (in)', 'Rain (in)'])
        df = pd.DataFrame.from_dict(weather_data, orient='index').reset_index()
        df.columns = ['time', 'ET0 (in)', 'Rain (in)']
        df['time'] = pd.to_datetime(df['time'])
        return df
    return pd.DataFrame(columns=['time', 'ET0 (in)', 'Rain (in)'])


def _weather_cache_keys(lat, lon):
    coord = _weather_coord_key(lat, lon)
    return f"cache_weather_hist::{coord}", f"cache_weather_lastfetch::{coord}"


def get_weather_history_cached(lat, lon):
    hist_key, _ = _weather_cache_keys(lat, lon)
    return _cache_get(hist_key, lambda: load_weather_history(lat, lon))


def get_weather_last_fetch_cached(lat, lon):
    _, fetch_key = _weather_cache_keys(lat, lon)
    return _cache_get(fetch_key, lambda: get_weather_last_fetch(lat, lon))


def invalidate_weather_cache(lat, lon):
    hist_key, fetch_key = _weather_cache_keys(lat, lon)
    _cache_invalidate(hist_key)
    _cache_invalidate(fetch_key)


# ==============================================================================
# 2. APPLICATION INITIALIZATION
# ==============================================================================

# 2.1 Load Local App Profiles (cached per user+property; mutated in place by the
# rename/add/remove/auto-save actions below, which persist those mutations via save_profiles())
profiles = _cache_get(f"cache_profiles::{st.session_state.user_id}::{active_prop}", load_profiles)


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
            "plant": "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)",
            "head_type": "Spray",
            "unit_count": 4,
            "plant_size": "Medium (typical shrub)",
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
                        WHERE property_name = :prop AND zone_name = :old_name AND user_id = :user_id
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
        # No st.rerun() here -- Streamlit already reruns automatically once an on_change
        # callback like this one finishes; calling it explicitly inside a callback is a no-op
        # (and prints a warning at the top of the page).


# 3.3 Action Callback: Add New Custom Zone
def handle_add_submit():
    custom_new_zone = st.session_state.add_input.strip()
    if custom_new_zone and custom_new_zone not in profiles:
        profiles[custom_new_zone] = {
            "zip": active_zip,
            "area": 1000,
            "flow": 5,
            "soil": "Loam",
            "plant": "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)",
            "head_type": "Spray",
            "unit_count": 4,
            "plant_size": "Medium (typical shrub)",
            "depth": 12,
            "mad": 50,
            "start_date": str(datetime.now().date())
        }
        save_profiles(profiles)
        st.toast(f"🌱 {custom_new_zone} added successfully!")
        # No st.rerun() here -- Streamlit already reruns automatically once an on_change
        # callback like this one finishes; calling it explicitly inside a callback is a no-op
        # (and prints a warning at the top of the page).


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
                        text("DELETE FROM zones WHERE property_name = :prop AND zone_name = :zone AND user_id = :user_id"),
                        {"prop": active_prop, "zone": active_zone_name, "user_id": st.session_state.user_id}
                    )
                    session.execute(
                        text("DELETE FROM watering_logs WHERE property_name = :prop AND zone_name = :zone AND user_id = :user_id"),
                        {"prop": active_prop, "zone": active_zone_name, "user_id": st.session_state.user_id}
                    )
                    session.commit()
            except Exception:
                pass
                
            if active_zone_name in profiles:
                profiles.pop(active_zone_name)
            
            save_profiles(profiles)
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
saved_plant = current_zone.get("plant", "Cool-Season Turf (Bluegrass, Fescue, Ryegrass)")
saved_head_type = current_zone.get("head_type", "Spray")
current_unit_count = int(current_zone.get("unit_count", 4))
saved_plant_size = current_zone.get("plant_size", "Medium (typical shrub)")
current_depth = int(current_zone.get("depth", 12))
current_mad = int(current_zone.get("mad", 50))
current_start_dt = current_zone.get("start_date", str(datetime.now().date()))

# 4.1 Easy / Advanced mode -- a session-level view toggle (same stored zone data either way,
# just fewer/derived inputs in Easy). Defaults to Advanced so today's behavior doesn't change
# for anyone who doesn't touch the toggle.
with st.sidebar:
    spec_mode = option_menu(
        menu_title=None,
        options=["Easy", "Advanced"],
        icons=["magic", "sliders"],
        menu_icon="cast",
        default_index=1,
        orientation="horizontal",
        key="spec_mode_menu"
    )

# 4.2 Plant Type -- first in both modes; same widget drives both, so it's always in sync.
plant_types = list(core_logic.PLANT_DATA.keys())
try:
    plant_index = plant_types.index(saved_plant)
except ValueError:
    plant_index = 0
plant_choice = st.sidebar.selectbox(
    "Plant Type",
    plant_types,
    index=plant_index,
    help="What's actually planted in this zone. Drives the crop coefficient (Kc) that scales reference ET down to real plant water use."
)

if spec_mode == "Easy":
    # Head type first -- it decides whether we're counting heads or plants next.
    head_types = list(core_logic.HEAD_TYPE_DATA.keys())
    try:
        head_type_index = head_types.index(saved_head_type)
    except ValueError:
        head_type_index = 0
    head_type_choice = st.sidebar.selectbox(
        "Irrigation Head Type",
        head_types,
        index=head_type_index,
        help="Spray heads sit closer together and use more GPM per sq ft than Rotor heads; Drip is sized off plant count instead of head count."
    )

    # Area isn't asked directly -- most homeowners can't measure a zone's square footage, but
    # they can walk out and count heads (or plants, for Drip). We derive area and flow from
    # that instead. See core_logic.estimate_area_and_flow.
    if head_type_choice == "Drip":
        plant_sizes = list(core_logic.PLANT_SIZE_AREA_SQFT.keys())
        try:
            plant_size_index = plant_sizes.index(saved_plant_size)
        except ValueError:
            plant_size_index = plant_sizes.index("Medium (typical shrub)")
        plant_size_choice = st.sidebar.selectbox(
            "Typical Plant Size",
            plant_sizes,
            index=plant_size_index,
            help="Drip emitters/dripline aren't something you can count the way sprinkler heads are, so this stands in for how much area each plant's root zone covers."
        )
        unit_count = st.sidebar.number_input(
            "Number of Plants on This Zone",
            min_value=1, value=current_unit_count, step=1, format="%d"
        )
    else:
        plant_size_choice = saved_plant_size  # not shown/used outside Drip, just carried over
        unit_count = st.sidebar.number_input(
            f"Number of {head_type_choice} Heads",
            min_value=1, value=current_unit_count, step=1, format="%d"
        )

    soil_types = list(core_logic.SOIL_DATA.keys())
    try:
        soil_index = soil_types.index(saved_soil)
    except ValueError:
        soil_index = soil_types.index("Loam")
    soil_choice = st.sidebar.selectbox("Soil", soil_types, index=soil_index)

    mad = st.sidebar.slider(
        "Manageable Allowable Depletion (MAD %)",
        min_value=10,
        max_value=80,
        value=current_mad,
        step=5,
        help="The percentage of soil water allowed to dry out before triggering an irrigation cycle."
    )

    # Depth isn't asked in Easy Mode -- defaulted from the selected plant type instead.
    depth_in = core_logic.PLANT_DATA.get(plant_choice, {}).get("DefaultDepth", 12)

    est_area, est_flow, est_radius, est_area_per_plant = core_logic.estimate_area_and_flow(
        head_type_choice, unit_count, plant_size_choice
    )
    area = max(1, est_area)
    flow = max(1, round(est_flow))

    if head_type_choice == "Drip":
        st.sidebar.caption(f"📐 Estimated area: **{area} sq ft** — {unit_count} plant(s) × ~{est_area_per_plant} sq ft each.")
        st.sidebar.caption(f"💧 Estimated flow: **{flow} GPM** — ~0.6 GPH/sq ft drip grid assumption.")
    else:
        st.sidebar.caption(f"📐 Estimated area: **{area} sq ft** — {unit_count} head(s) × {est_radius:.1f}² ft radius each.")
        st.sidebar.caption(f"💧 Estimated flow: **{flow} GPM** — {unit_count} head(s) × {core_logic.HEAD_TYPE_DATA[head_type_choice]['avg_gpm_per_head']:.1f} GPM/head.")

    if flow > 15:
        st.sidebar.warning(f"⚠️ {flow} GPM is a lot for one zone — most homes can't reliably supply this. Consider Rotor heads (fewer, longer-throw) instead of Spray, or fewer units per zone.")

    head_type_for_save = head_type_choice
    unit_count_for_save = unit_count
    plant_size_for_save = plant_size_choice

else:  # Advanced
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

    # Not editable in Advanced -- carried over from Easy Mode's last selection (or the zone default).
    head_type_for_save = saved_head_type
    unit_count_for_save = current_unit_count
    plant_size_for_save = saved_plant_size

# Auto-Save Engine: Updates automatically if any parameter is altered
if (area != current_area or
    flow != current_flow or
    soil_choice != saved_soil or
    plant_choice != saved_plant or
    head_type_for_save != saved_head_type or
    unit_count_for_save != current_unit_count or
    plant_size_for_save != saved_plant_size or
    depth_in != current_depth or
    mad != current_mad):

    profiles[active_zone_name] = {
        "zip": active_zip,
        "area": area,
        "flow": flow,
        "soil": soil_choice,
        "plant": plant_choice,
        "head_type": head_type_for_save,
        "unit_count": unit_count_for_save,
        "plant_size": plant_size_for_save,
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

plant_info = core_logic.PLANT_DATA.get(plant_choice, core_logic.PLANT_DATA["Cool-Season Turf (Bluegrass, Fescue, Ryegrass)"])
kc_value = plant_info["Kc"]



# ==============================================================================
# 7. WEATHER ENGINE (API & DATA SYNCHRONIZATION)
# ==============================================================================

# 7.1 Setup API Sessions
# Keep the request cache warm for a full day so the dashboard reuses the same weather payload
# instead of creating a fresh Open-Meteo request for every rerun or log action.
cache_session = requests_cache.CachedSession('.cache', expire_after=86400)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)


# 7.2 Core Integrated Fetching Routine
@st.cache_data(ttl=86400)
def fetch_weather_integrated(lat, lon, start_date_str, weather_log_path):
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
            # Only request as many past days as the gap actually requires (capped at 92) --
            # Open-Meteo bills a request as multiple "API calls" once its span passes 2 weeks,
            # so requesting the full 92 every time (as this used to) was ~7-8x the quota cost
            # of a normal daily top-up fetch.
            "past_days": min(92, max(days_back, 1)),
            "forecast_days": 14
        }
        is_hourly = True

    try:
        print(f"[OpenMeteo] network fetch: lat={lat} lon={lon} url={url} "
              f"span={params.get('past_days', (today - start_dt).days)}d+forecast start={start_dt} end={today}")
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

# Always load the archive first (cloud cache if connected, else local JSON) -- this is what
# actually backs the displayed history/forecast, so the live API only ever needs to fill in
# what's missing from it. Session-cached: only a real fetch below (at most every ~20h)
# invalidates and re-reads this, so unrelated reruns don't re-hit the DB for it.
df_permanent = get_weather_history_cached(lat, lon)

zone_start_date = pd.to_datetime(
    current_zone.get("start_date", str(datetime.now().date() - pd.Timedelta(days=7)))
).date()

# Base the "how far back is already covered" check only on archived history, not the forecast
# rows also stored in the archive (their dates run into the future and would otherwise make
# the gap look already closed).
today_date = datetime.now().date()
historical = df_permanent[pd.to_datetime(df_permanent['time']).dt.date <= today_date] if not df_permanent.empty else df_permanent

if not historical.empty:
    # Only ask Open-Meteo for a couple of days of overlap past what's already archived,
    # instead of re-requesting the zone's entire history on every fetch.
    overlap_start_date = (pd.to_datetime(historical['time']).max() - pd.Timedelta(days=2)).date()
    effective_start_date = max(zone_start_date, overlap_start_date)
else:
    # No history archived yet for this location -- do the one-time full backfill from the zone's start date.
    effective_start_date = zone_start_date

# Durable gate: skip the network call entirely if this location was already fetched recently.
# This lives in the DB/local-JSON (not st.cache_data or the requests_cache sqlite file), so it
# still holds even after a Streamlit Cloud container reboot wipes those in-process caches.
last_fetch = get_weather_last_fetch_cached(lat, lon)
needs_fetch = last_fetch is None or (datetime.utcnow() - last_fetch) > timedelta(hours=20)

df_api = None
if needs_fetch:
    df_api = fetch_weather_integrated(lat, lon, str(effective_start_date), WEATHER_LOG)

if df_api is not None:
    archive_weather(df_api, lat, lon)
    # A real fetch just landed -- force one fresh read so the session cache picks up what
    # was just archived instead of serving the pre-fetch snapshot for the rest of this session.
    invalidate_weather_cache(lat, lon)
    df_permanent = get_weather_history_cached(lat, lon)  # reload after archiving
    df_daily = pd.concat([df_permanent, df_api]).drop_duplicates(subset='time', keep='last').sort_values('time')
elif not df_permanent.empty:
    df_daily = df_permanent.sort_values('time')
if df_api is not None or not df_permanent.empty:
    df_daily['time'] = pd.to_datetime(df_daily['time']).dt.normalize()
    
    # Enforce threshold constraints to retain structural history
    earliest_allowed_date = pd.Timestamp(datetime.now().date()) - pd.Timedelta(days=180)
    df_daily = df_daily[df_daily['time'] >= earliest_allowed_date]
    
    # Merge local zone schedules and database records
    all_logs = get_logs_cached()
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
    if not zone_logs:
        # Never-watered zone: assume field capacity (0" deficit) rather than running the
        # warmup loop below, which pulls in real historical ET/rain from before this zone
        # existed. That antecedent-moisture backfill is only meaningful once there's an
        # actual watering history to reconcile it against -- for a brand-new zone it just
        # produces a nonzero "deficit" the app has no real evidence for.
        current_deficit = 0.0
    else:
        # Anchor the rolling window at the first real watering event, not zone_start_date
        # minus a 7-day pre-buffer. The first log is the last known moment the soil was
        # actually brought toward field capacity; backfilling ET debt from before it (e.g.
        # a whole week predating the zone's own creation) produced an inflated deficit the
        # app had no real evidence for the instant someone logged their first watering.
        first_log_ts = pd.to_datetime(min(log["date"] for log in zone_logs)).normalize()
        mask = (df_daily['time'] >= first_log_ts) & (df_daily['time'] <= today_dt)
        zone_weather = df_daily.loc[mask].sort_values('time')

        running_deficit = 0.0
        for idx, row in zone_weather.iterrows():
            etc_in = row['ET0 (in)'] * kc_value  # crop-adjusted water use, not raw reference ET0
            running_deficit += etc_in
            running_deficit -= (row['Rain (in)'] + row['Irrigation (in)'])
            if running_deficit < 0:
                running_deficit = 0.0

        current_deficit = running_deficit
    gallons = current_deficit * area * 0.623
    runtime = gallons / flow if flow > 0 else 0


# ==============================================================================
# 9. MAIN DASHBOARD METRICS
# ==============================================================================
    if selected_tab == "Dashboard":
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
        elif current_deficit <= 0 and not zone_logs:
            # Never-watered zone reading 0" is an assumption (field capacity), not a measurement --
            # pair it with a reference runtime computed as if the zone were bone dry (PWP) so a
            # first-time user has a number to start from instead of just "you're fine, do nothing."
            full_gallons = paw_total * area * 0.623
            full_runtime = full_gallons / flow if flow > 0 else 0
            st.info(f"🌱 **New Zone:** Starting at 0\" deficit (assumed field capacity) until real readings accumulate. If this zone were completely dry, a full soak would take an estimated **{full_runtime:.1f} minutes** at current settings.")
        elif current_deficit <= 0:
            st.success(f"✅ **Soil is Hydrated!**")
        else:
            st.info(f"⏱️ **Irrigation Plan:** Run for **{runtime:.1f} minutes** to refill root zone capacity.")


# ==============================================================================
# 10. VISUAL ANALYTICS & TABLES
# ==============================================================================
if selected_tab == "Dashboard":
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
                           scale=alt.Scale(domain=[view_start.strftime('%Y-%m-%d'), view_end.strftime('%Y-%m-%d')]),
                           axis=alt.Axis(format='%b %d'))

            # Chart Layer A: Evapotranspiration Line
            et_chart = alt.Chart(df_zoom).mark_line(strokeWidth=3, color='#FF8C00').encode(
                x=x_axis,
                y=alt.Y('ET0 (in):Q', title='Inches'),
                tooltip=['time:T', 'ET0 (in):Q']
            )

            # Chart Layer B: Rainfall Bars
            rain_chart = alt.Chart(df_zoom).mark_bar(opacity=0.5, color='#ADD8E6').encode(
                x=x_axis,
                y='Rain (in):Q',
                tooltip=['time:T', 'Rain (in):Q']
            )

            # Chart Layer C: Irrigation Events
            irr_chart = alt.Chart(df_zoom).mark_bar(size=10, color='#003366').encode(
                x=x_axis,
                y='Irrigation (in):Q',
                tooltip=['time:T', 'Irrigation (in):Q']
            )

            # Chart Layer D: Today Reference Marker
            today_line = alt.Chart(pd.DataFrame({'time': [now_dt]})).mark_rule(
                color='red', strokeDash=[5,5], strokeWidth=2
            ).encode(x=x_axis)


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
if selected_tab == "Ledger":
    st.divider()
    st.header("📈 Global Water Usage Tracker")

    all_logs = get_logs_cached()

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
                        invalidate_logs_cache()
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
if selected_tab == "Reference":
    # Ordered to follow the actual pipeline: the three input domains (soil, plant,
    # weather) first, then the calculation tab that combines all three, then sourcing.
    tab_audit, tab_plant, tab_science, tab_calc, tab_refs = st.tabs([
        "📊 Soil Physics Logic",
        "🌱 Plant Water Use",
        "🔬 Weather Science",
        "🧮 Calculation Logic",
        "📚 References"
    ])

    with tab_audit:
        st.write(f"### Soil Profile: {soil_choice}")
        st.info("💡 **How we size your soil's 'water tank' from its texture:**")
        st.markdown("""
        Every soil texture holds a different amount of water against gravity (**Field
        Capacity**) and a different amount still physically available to roots before
        wilting (**Permanent Wilting Point**). The gap between the two, scaled to the
        active root depth, is the total water "tank" this zone draws from before the
        **Manageable Allowable Depletion (MAD)** threshold triggers an irrigation cycle.
        """)
        st.latex(r"AW = FC - PWP")
        st.caption(f"Available Water: {fc_inft/12:.3f} - {pwp_inft/12:.3f} = **{aw_per_foot/12:.3f} in/in**")
        st.latex(r"PAW = AW \times RZ")
        st.caption(f"Plant Available Water: {aw_per_foot:.2f} in/ft × {rz_ft:.2f} ft = **{paw_total:.2f} inches**")
        st.latex(r"AD = PAW \times MAD")
        st.caption(f"Allowable Depletion: {paw_total:.2f} in × {mad/100:.2f} = **{ad_limit:.2f} inches**")

        st.write("#### Soil Reference Table (all soil types)")
        soil_df = pd.DataFrame([
            {
                "Soil Type": name,
                "FC (in/ft)": info["FC"] * 12,
                "PWP (in/ft)": info["PWP"] * 12,
                "AW (in/ft)": (info["FC"] - info["PWP"]) * 12,
            }
            for name, info in core_logic.SOIL_DATA.items()
        ])
        st.dataframe(
            soil_df.style.apply(
                lambda row: ["background-color: rgba(0,150,0,0.15)" if row["Soil Type"] == soil_choice else "" for _ in row],
                axis=1
            ).format({"FC (in/ft)": "{:.2f}", "PWP (in/ft)": "{:.2f}", "AW (in/ft)": "{:.2f}"}),
            hide_index=True,
            use_container_width=True
        )
        depletion_status = (current_deficit / ad_limit) * 100 if ad_limit > 0 else 0
        st.caption(f"**Current Status:** Your deficit is {current_deficit:.2f}\", or {depletion_status:.1f}% of your Allowable Depletion limit. Soil constants are Saxton & Rawls (2006) texture-class estimates — see References tab.")

    with tab_plant:
        st.write(f"### Plant Profile: {plant_choice}")
        st.info("💡 **How we scale atmospheric water loss down to what this plant actually uses:**")
        st.markdown("""
        The weather engine reports **ET₀** — reference evapotranspiration, the water a
        standardized well-watered grass reference surface would lose. Different plants use
        meaningfully less (or, rarely, more) than that reference. The **crop coefficient (Kc)**
        scales ET₀ into **ETc**, the actual water use for what's planted in this zone, before
        it's added to the daily deficit.
        """)
        st.latex(r"ET_c = ET_0 \times K_c")
        st.caption(f"For {plant_choice}: ET₀ × **{kc_value:.2f}** = ETc")

        st.write("#### Kc Reference Table (all plant types)")
        plant_df = pd.DataFrame([
            {"Plant Type": name, "Kc": info["Kc"]}
            for name, info in core_logic.PLANT_DATA.items()
        ])
        st.dataframe(
            plant_df.style.apply(
                lambda row: ["background-color: rgba(0,150,0,0.15)" if row["Plant Type"] == plant_choice else "" for _ in row],
                axis=1
            ).format({"Kc": "{:.1f}"}),
            hide_index=True,
            use_container_width=True
        )
        st.caption("Kc values are mid-season approximations from FAO-56 (turf/tree ranges) and the WUCOLS landscape-coefficient method (ornamental groupings) — see References tab.")

    with tab_science:
        st.write(f"### Weather Profile: {active_zip}")
        st.info("💡 **Where the daily ET₀ and rain numbers actually come from:**")
        st.markdown("""
        Reference evapotranspiration (**ET₀**) estimates how much water a well-watered
        reference grass surface loses to the atmosphere on a given day, driven by solar
        radiation, temperature, humidity, and wind. Open-Meteo computes it upstream with
        the **FAO-56 Penman-Monteith equation** and this app pulls in the finished daily
        value as an input — the Penman-Monteith math itself doesn't run locally, ET₀ just
        feeds the Kc scaling and deficit loop covered in the other tabs.
        """)
        st.latex(r"ET_0 = \frac{0.408\,\Delta(R_n-G) + \gamma \frac{900}{T+273} u_2 (e_s - e_a)}{\Delta + \gamma(1+0.34u_2)}")
        if not df_history.empty:
            last_row = df_history.sort_values('time').iloc[-1]
            st.caption(f"Most recent fetched day ({pd.to_datetime(last_row['time']).date()}): ET₀ = **{last_row['ET0 (in)']:.2f} in**, Rain = **{last_row['Rain (in)']:.2f} in**")

        st.write("#### Environmental Inputs Tracked")
        inputs_df = pd.DataFrame({
            "Input": ["Solar Radiation", "Ambient Temperature", "Relative Humidity", "Wind Speed"],
            "Role": [
                "Primary thermodynamic energy engine",
                "Temperature gradient driving vapor pressure",
                "Dry atmospheric vapor pressure gradient",
                "Boundary layer transport factor",
            ],
        })
        st.dataframe(inputs_df, hide_index=True, use_container_width=True)
        st.caption("Data Source: High-resolution Open-Meteo meteorological API, computed per FAO-56 — see References tab.")

    with tab_calc:
        st.write(f"### Water Balance: {active_zone_name}")
        st.info("💡 **How the daily deficit becomes a sprinkler runtime:**")
        st.markdown("""
        Every day, this zone's soil moisture deficit grows by crop-adjusted water use
        (**ETc**, from the Plant Water Use tab) and shrinks by rain plus any logged
        irrigation, floored at field capacity (0" deficit — it never carries below that,
        even after a large rain event). Once you're ready to water, that deficit converts
        directly into a water volume and a sprinkler runtime using the zone's area and
        flow rate.

        **Never-watered zones are the one exception.** A zone with no logged watering
        history skips this loop entirely and reads a flat 0" deficit, rather than
        back-filling real historical ET/rain from before the zone existed. That
        antecedent-moisture backfill only means something once there's an actual watering
        history to reconcile it against — for a brand-new zone it would otherwise produce a
        deficit the app has no real evidence for.
        """)
        st.latex(r"Deficit_{day} = Deficit_{day-1} + (ET_0 \times K_c) - (Rain + Irrigation)")
        st.caption(f"This zone's **{plant_choice}** Kc of **{kc_value:.2f}** is baked into every day of that loop — see the *Plant Water Use* tab.")
        st.latex(r"Gallons = Deficit \times Area \times 0.623")
        st.caption(f"Water Volume: {current_deficit:.3f}\" × {area} sq ft × 0.623 = **{gallons:.1f} Gallons**")
        st.latex(r"Runtime = \frac{Gallons}{Flow}")
        st.caption(f"Runtime: {gallons:.1f} gal / {flow} GPM = **{runtime:.1f} Minutes**")

        st.write("#### Runtime Breakdown")
        st.dataframe(pd.DataFrame({
            "Step": ["Net Depth Needed", "Water Volume", "Runtime"],
            "Value": [f"{current_deficit:.3f}\"", f"{gallons:.1f} gal", f"{runtime:.1f} min"]
        }), hide_index=True, use_container_width=True)

        st.write("#### How Area & Flow Are Determined (Easy vs. Advanced Mode)")
        st.info("💡 **Why this zone's area and GPM are what they are:**")
        st.markdown("""
        Advanced Mode asks for Zone Area and Zone Flow directly. Easy Mode asks for neither —
        most homeowners can't measure a zone's square footage or know its flow rate, but they
        *can* walk outside and count something. For Spray/Rotor, that's the number of heads on
        the zone; for Drip, it's the number of plants (individual emitters or dripline footage
        aren't practically countable the way heads are). Both area and flow are then derived
        from that count instead of asked for.
        """)
        st.latex(r"Area \approx Heads \times Radius^2 \qquad Flow_{GPM} \approx Heads \times GPM_{per\ head}")
        st.caption("Radius/GPM-per-head are averages for the selected head type; spacing = radius follows the same \"50% of diameter\" convention manufacturers use to publish matched-precipitation rates.")
        st.latex(r"Area \approx Plants \times Area_{per\ plant} \qquad Flow_{GPM} \approx \frac{Area \times 0.6\ GPH/sq\ ft}{60}")
        st.caption("Drip instead sizes area off plant count × a typical coverage footprint per plant size, then applies the same flat drip rate used in the Calculation Logic math above.")

        if spec_mode == "Easy":
            if head_type_choice == "Drip":
                st.success(f"**This zone (Easy Mode):** {unit_count} plant(s) × ~{est_area_per_plant} sq ft each ≈ **{area} sq ft** → **{flow} GPM**")
            else:
                head_gpm = core_logic.HEAD_TYPE_DATA[head_type_choice]["avg_gpm_per_head"]
                st.success(f"**This zone (Easy Mode):** {unit_count} {head_type_choice} head(s) × {est_radius:.1f}² ft ≈ **{area} sq ft**, × {head_gpm:.1f} GPM/head ≈ **{flow} GPM**")
        else:
            st.caption("This zone is in Advanced Mode right now, where Area and Flow are entered directly — switch to Easy Mode in the sidebar to see this zone's live estimate.")

        head_ref_df = pd.DataFrame([
            {"Head Type": "Spray", "Avg GPM/head": core_logic.HEAD_TYPE_DATA["Spray"]["avg_gpm_per_head"], "Avg Radius (ft)": core_logic.HEAD_TYPE_DATA["Spray"]["avg_radius_ft"], "Basis": "Per head"},
            {"Head Type": "Rotor", "Avg GPM/head": core_logic.HEAD_TYPE_DATA["Rotor"]["avg_gpm_per_head"], "Avg Radius (ft)": core_logic.HEAD_TYPE_DATA["Rotor"]["avg_radius_ft"], "Basis": "Per head"},
            {"Head Type": "Drip", "Avg GPM/head": None, "Avg Radius (ft)": None, "Basis": f"{core_logic.HEAD_TYPE_DATA['Drip']['gph_per_sqft']:.1f} GPH/sq ft flat rate"},
        ])
        st.dataframe(
            head_ref_df.style.apply(
                lambda row: ["background-color: rgba(0,150,0,0.15)" if row["Head Type"] == head_type_for_save else "" for _ in row],
                axis=1
            ).format({"Avg GPM/head": "{:.1f}", "Avg Radius (ft)": "{:.1f}"}, na_rep="—"),
            hide_index=True,
            use_container_width=True
        )
        st.caption("Averages compiled from Rain Bird, Hunter, Toro, and Orbit residential nozzle catalogs (irrigation_head_specs/head_database.csv in the repo) — see Reference E.")

        st.write("#### Manual Watering Log Conversion")
        st.markdown("Logging a runtime in minutes uses the matched-precipitation-rate formula to convert flow and area into an applied depth:")
        st.latex(r"Rate_{in/hr} = \frac{96.25 \times GPM}{Area}")
        manual_rate = (96.25 * flow / area) if area > 0 else 0
        st.caption(f"This zone's rate: (96.25 × {flow}) / {area} = **{manual_rate:.3f} in/hr**. This app uses the unrounded 96.25 constant rather than the commonly published rounded 96.3 — same formula, tighter precision.")
        st.caption("Precipitation-rate methodology follows Irrigation Association matched-precipitation guidance — see References tab.")

    with tab_refs:
        st.write("### References")
        st.markdown("""
        **Reference A: Soil Physics — Estimating Soil Water Characteristics**
        * Saxton, Keith S., and Walter J. Rawls. 'Estimating Soil Water Characteristics from Texture, Organic Matter, and Salinity.' *Soil Science Society of America Journal*, vol. 70, no. 5, 2006, pp. 1569-1578.

        **Reference B: Plant Water Use — Landscape Crop Coefficients (Kc)**
        * Costello, Lawrence R., and Katherine S. Jones. *WUCOLS IV: Water Use Classification of Landscape Species*. California Center for Urban Horticulture, UC Davis, 2014.

        **Reference C: Weather Science — Reference Crop ET Evaluations**
        * Allen, Richard G., et al. 'Crop Evapotranspiration: Guidelines for Computing Crop Water Requirements.' *FAO Irrigation and Drainage Paper 56*, 1998.

        **Reference D: Calculation Logic — Precipitation Rate & Runtime Methodology**
        * Irrigation Association and American Society of Irrigation Consultants. *Landscape Irrigation Best Management Practices*. 2014. [irrigation.org](https://www.irrigation.org/IA/FileUploads/IA/Certification/BMPDesign_Install_Manage.pdf)
        * Irrigation Association. *Turf and Landscape Irrigation Best Management Practices*. 2002 (rev.).

        **Reference E: Easy Mode — Irrigation Head Flow & Radius Averages**
        * Rain Bird, Hunter, Toro, and Orbit residential nozzle catalogs (spray, rotor, and drip product lines), compiled 2026-07-18. Full source datasheets and per-nozzle data in `irrigation_head_specs/` in this repo — see `SOURCES.md` there for the manufacturer-by-manufacturer breakdown.
        """)


# ==============================================================================
# 13. SYSTEM SECURITY & BACKUP UTILITIES
# ==============================================================================
if selected_tab == "Properties":
    st.divider()
    with st.expander("🛡️ Data Security & Backups"):
        st.write("### 📥 Download App Data")
        st.caption("Export active profiles and logging archives safely to disk.")

        profiles_string = json.dumps(profiles, indent=4)
        all_logs = get_logs_cached()
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
mode_text = "local" if isinstance(conn, MockConnection) else "cloud"
st.markdown(
    f"""
    <div style='text-align: center; color: gray; font-size: 0.8em;'>
        Irrigation Dashboard • {mode_text} • v0.37
    </div>
    """,
    unsafe_allow_html=True
)