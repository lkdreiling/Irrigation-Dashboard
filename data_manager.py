import os
import json

# 1. Define the paths FIRST
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "IrrigationData")
SYSTEM_DIR = os.path.join(DATA_DIR, "SystemData")
BACKUP_DIR = os.path.join(BASE_DIR, "Backups")

# 2. Now that the names exist, create the directories
for folder in [DATA_DIR, SYSTEM_DIR, BACKUP_DIR]:
    os.makedirs(folder, exist_ok=True)

# 3. Define file paths using those directories
PROP_LIST_FILE = os.path.join(SYSTEM_DIR, "properties.json")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f: return json.load(f)
        except: return default
    return default

def save_json(path, data):
    with open(path, "w") as f: 
        json.dump(data, f, indent=4)

def get_prop_paths(active_prop):
    return {
        "db": os.path.join(DATA_DIR, f"{active_prop}_profiles.json"),
        "log": os.path.join(DATA_DIR, f"{active_prop}_log.json"),
        "weather": os.path.join(SYSTEM_DIR, f"{active_prop}_weather.json")
    }

def save_properties_master(prop_data):
    """Saves the master property dictionary (Property Name: Zip Code)"""
    save_json(PROP_LIST_FILE, prop_data)
