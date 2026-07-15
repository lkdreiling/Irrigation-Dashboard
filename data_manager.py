import os
import json
import hashlib

# 1. Define the paths FIRST
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "IrrigationData")
SYSTEM_DIR = os.path.join(DATA_DIR, "SystemData")
BACKUP_DIR = os.path.join(BASE_DIR, "Backups")

# 2. Now that the names exist, create the directories
for folder in [DATA_DIR, SYSTEM_DIR, BACKUP_DIR]:
    os.makedirs(folder, exist_ok=True)

# 3. Define global files
PROP_LIST_FILE = os.path.join(SYSTEM_DIR, "properties.json")
CREDENTIALS_FILE = os.path.join(SYSTEM_DIR, "users.json")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f: 
                return json.load(f)
        except: 
            return default
    return default

def save_json(path, data):
    with open(path, "w") as f: 
        json.dump(data, f, indent=4)

# 4. User Path Resolution (File Isolation)
def get_user_paths(user_id):
    """Returns directory and configuration file paths isolated for a specific user."""
    # Ensure username is clean for path names
    clean_username = "".join([c for c in user_id if c.isalpha() or c.isdigit() or c=='_']).strip()
    user_dir = os.path.join(DATA_DIR, "users", clean_username)
    user_system_dir = os.path.join(user_dir, "SystemData")
    os.makedirs(user_dir, exist_ok=True)
    os.makedirs(user_system_dir, exist_ok=True)
    return {
        "user_dir": user_dir,
        "user_system_dir": user_system_dir,
        "prop_list": os.path.join(user_system_dir, "properties.json")
    }

def get_prop_paths_for_user(user_id, active_prop):
    """Returns database (profile), log, and weather paths isolated under the user's specific folder."""
    user_paths = get_user_paths(user_id)
    return {
        "db": os.path.join(user_paths["user_dir"], f"{active_prop}_profiles.json"),
        "log": os.path.join(user_paths["user_dir"], f"{active_prop}_log.json"),
        "weather": os.path.join(user_paths["user_system_dir"], f"{active_prop}_weather.json")
    }

# 5. Password Hashing & Verification
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

# 6. Local User Database Helpers
def register_user_local(username, password):
    """Registers a new user in the local users.json credentials database."""
    users = load_json(CREDENTIALS_FILE, {})
    username_lower = username.strip().lower()
    if username_lower in users:
        return False
    users[username_lower] = hash_password(password)
    save_json(CREDENTIALS_FILE, users)
    return True

def authenticate_user_local(username, password):
    """Authenticates a user against the local credentials database."""
    users = load_json(CREDENTIALS_FILE, {})
    username_lower = username.strip().lower()
    if username_lower not in users:
        return False
    return verify_password(password, users[username_lower])
