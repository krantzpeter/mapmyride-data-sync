# MapMyRide Data Sync & Mapping Tool

This project is a Python-based tool designed to synchronize workout data from a MapMyRide account, store it locally, and generate interactive maps of the GPS tracks.

## Features

- **Automated Web Scraping:** Uses Selenium to log in to MapMyRide and download workout data.
- **Just-in-Time Login:** The web client only launches a browser and logs in when a download is actually required, making offline operations fast.
- **Robust Data Management:**
    - Stores all workout metadata in a master CSV file (`pk_workouts.csv`).
    - Downloads and archives all TCX track files to a local directory.
    - Includes a "Full Sync" mode to verify data integrity and a "Quick Sync" mode for adding new workouts rapidly.
- **Geospatial Processing:**
    - Parses TCX files to extract GPS coordinates.
    - Simplifies complex GPS tracks into lightweight GeoJSON files for efficient mapping.
- **Interactive Map Generation:** Uses Folium to create a single `all_routes.html` file that visualizes all walk/hike routes on an interactive map.
- **Secure Credential Management:** Uses environment variables to keep login credentials separate from the source code.

## Important Note: CAPTCHA Requirement

Due to MapMyRide's security measures, the initial login will likely be stopped by a CAPTCHA challenge. The script will pause for up to 120 seconds, giving you time to **manually solve the CAPTCHA** in the browser window that Selenium opens. Once you complete it, the script will automatically proceed with the login.

## Setup

Follow these steps to get the project running on your local machine.

1.  **Clone the repository:**
2.  **Create and Activate a Virtual Environment:**
    This isolates the project's dependencies from your system's Python.
3.  **Install Dependencies:**
    This command installs all the required libraries from the `requirements.txt` file.
4. **Set Environment Variables for Credentials:** For security, your MapMyRide login details are not stored in the code. You must set them as environment variables.  
**Note**: For a permanent solution, add these to your system's environment variables or your shell's profile file (.zshrc, .bash_profile, etc.).
5. **Configure Paths:** The project uses a config.ini file to know where to save your data:
- Create a copy of config.ini.template and rename it to config.ini.
- Open config.ini in a text editor.
- Fill in the required paths for tcx_archive_path, simplified_gps_track_folder, and project_path as described by the comments in the file.
       