# 🌱 Irrigation Dashboard v3.2

Irrigation Dashboard is a localized, web-based Evapotranspiration (ET) application that helps you manage lawn and garden watering with precision. By pulling real-time weather data and calculating soil moisture depletion, it tells you exactly **when** to water and **how much** to apply.

## 🚀 Quick Start (Web App)

The easiest way to use the dashboard is directly through your web browser via **Streamlit Cloud**:

1. **Launch the App:** Open your deployment URL (e.g., `https://your-app-name.streamlit.app`).
2. **Configure Your Property:** Click on the **Property Settings** panel to input your local Zip Code.
3. **Add Zones:** Fill out your crop/landscape characteristics, square footage, and irrigation flow rates to begin calculations immediately.

*Note: Your session data is completely isolated. Test parameters or modifications made by other active sessions will not leak into or override your configuration profiles.*

## 🛠️ How it Works

The system uses three core components to calculate your irrigation needs:
* **Weather Engine:** Fetches ET0 and Rainfall data from Open-Meteo based on your Zip Code, caching weather requests locally to minimize bandwidth.
* **Soil Math:** Uses audited hydraulic properties to calculate Plant Available Water (PAW) and Allowable Depletion based on your specific soil texture classification.
* **Sequential Deficit Tracker:** Implements a true checkbook method that simulates daily root zone saturation. By evaluating weather history day-by-day chronologically, any water gain that exceeds field capacity is discarded as deep drainage or runoff rather than accumulating as "infinite credit."

## 📥 Backups & Data Security

Because cloud server environments are dynamic and reset occasionally, saving your configurations is highly streamlined:
* **Download JSON Ledgers:** Scroll to the **Data Security & Backups** expander at the bottom of the dashboard to download your exact `Zone Profiles` and `Watering Logs` directly to your local computer as backup files.
* **Restore Data:** You can re-upload your saved configuration files to restore your properties instantly if the cloud instance resets.

## 📂 Project Structure

* `et_dashboard.py`: The main web interface, routing logic, and user controls.
* `core_logic.py`: Handles soil physics math and coordinate lookups.
* `data_manager.py`: Manages file storage, session isolation rules, and directory verification.
* `IrrigationData/`: (Auto-generated) Stores property configurations, zone profiles, and watering history log ledgers.

---

## 💻 Alternative Local Setup (Optional)

If you wish to host the dashboard locally on your machine instead of using the web application:

1. **Download the Code:** Download this repository as a ZIP file and extract it.
2. **Environment:** Ensure you have [Anaconda](https://www.anaconda.com/) or Python 3.10+ installed.
3. **Launch:** Double-click the `Run_Irrigation.bat` file to automatically install libraries and open the system on `localhost`.
4. **Local Directory Button:** When running locally on Windows, an extra button appears in the **Data Security** tab allowing you to jump directly to your data folder via Windows File Explorer.

---
*Developed for smart water management and healthy landscapes.*