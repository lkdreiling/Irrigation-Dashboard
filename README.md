# 🌱 Irrigation Dashboard

Irrigation Dashboard is a localized Evapotranspiration (ET) dashboard that helps you manage lawn and garden watering with precision. By pulling real-time weather data and calculating soil moisture depletion, it tells you exactly **when** to water and **how much** to apply.

## 🚀 Quick Start (Windows)

1. **Download the Code:** Download this repository as a ZIP file and extract it to a folder on your computer.
2. **Install Python:** Ensure you have [Python 3.10+](https://www.python.org/downloads/) or [Anaconda](https://www.anaconda.com/) installed.
3. **Run the App:** Double-click the `Run_Irrigation.bat` file. 
   - *The script will automatically install necessary libraries and launch the dashboard in your web browser.*

## 🛠️ How it Works

The system uses three core components to calculate your irrigation needs:
* **Weather Engine:** Fetches ET0 and Rainfall data from Open-Meteo based on your Zip Code.
* **Soil Math:** Calculates Plant Available Water (PAW) and Allowable Depletion based on your specific soil type (Loam, Clay, Sand, etc.).
* **Deficit Tracker:** Tracks the "water balance" by subtracting ET from Rainfall and your logged Irrigation events.

## 📂 Project Structure

* `et_dashboard.py`: The main visual interface and user controls.
* `core_logic.py`: Handles soil calculations and coordinate lookups.
* `data_manager.py`: Manages file storage, folder creation, and backups.
* `IrrigationData/`: (Auto-generated) Stores your specific zone profiles and watering logs.

## ⚙️ Setup Tips

* **Zip Code:** On first launch, use the "Property Settings" popover to set your local Zip Code.
* **Zone Profiles:** Set your square footage and GPM (Gallons Per Minute) for accurate runtime calculations.
* **Backups:** Use the "Data Security" tab at the bottom of the dashboard to create instant backups of your data.

## 🎨 Make it an App (Optional)

To give the **Irrigation Dashboard** a professional look on your Windows desktop, follow these steps to create a custom shortcut using the provided emblem:

1. **Create the Shortcut**:
   - Navigate to your project folder.
   - Right-click `run_dashboard.bat` and select **Send to** > **Desktop (create shortcut)**.

2. **Apply the Icon**:
   - Go to your Desktop and right-click the new shortcut.
   - Select **Properties**.
   - Under the **Shortcut** tab, click the **Change Icon...** button.
   - Click **Browse** and navigate to the `Misc/` folder in your project directory.
   - Select the `emblem.ico` file and click **Open**, then **OK**.

3. **Final Polish**:
   - Rename the shortcut to **Irrigation Dashboard**.
   - (Optional) Pin it to your Taskbar or Start Menu for one-click access.

---
*Developed for smart water management and healthy landscapes.*
