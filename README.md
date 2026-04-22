# MapMyRide Data Sync & Mapping Tool

This project is a Python-based tool designed to synchronize workout data from a MapMyRide account, store it locally, and generate interactive maps of the GPS tracks. 

## Features

- Automated Web Scraping: Uses Selenium with an optimized "Metadata Theft" strategy to fetch proper workout titles. 
- Just-in-Time Login: The web client only launches a browser and logs in when a download or scrape is actually required. 
- Robust Data Management: 
  - Stores workout metadata in a master CSV file (pk_workouts.csv) without bloating the schema. 
  - Downloads and archives TCX track files with descriptive, standardized naming. 
  - Includes a "Full Sync" mode for comprehensive maintenance and a "Quick Sync" for daily updates. 
- Geospatial Processing: 
  - Parses TCX files and simplifies complex tracks into lightweight GeoJSON files. 
- Interactive Map Generation: Uses Folium to create an all_routes.html dashboard visualizing all hikes and walks. 

## Important Note: CAPTCHA Requirement

Due to MapMyRide's security measures, the initial login will likely be stopped by a CAPTCHA challenge. The script will pause for up to 120 seconds, giving you time to manually solve the CAPTCHA in the browser window. Once completed, the script automatically proceeds. 

## Setup

1. Clone the repository. 
2. Virtual Environment: Create and activate a Python 3.11+ environment. 
3. Install Dependencies: pip install -r requirements.txt (Uses the last FOSS version of PySimpleGUI). 
4. Environment Variables: Set MAPMYRIDE_USERNAME and MAPMYRIDE_PASSWORD on your system. 
5. Configure Paths: Rename config.ini.template to config.ini and fill in your local folder paths. 

## GPXSEE & FILENAME LOGIC:

- Standardized Naming: Files are named as yyyy mm dd <Title> <Distance>km <Activity> (W[ID]).tcx. 
- Manual Renaming: You may manually rename TCX files in Explorer. As long as you retain the (W[ID]) suffix, the tool will automatically recover the descriptive title from the filename at runtime. 
- Proper Names: The tool prioritizes the scraped "Proper Name" from the website or the filename over the generic "Notes" field. 
- Simplified Folder: Manual renames do not flow through to the 'Simplified' folder automatically. Delete the corresponding GeoJSON file to trigger a re-generation with the new name. 

## Usage

Run via terminal: python main.py 

### Sync Modes Comparison

| Feature                  | Quick Sync (full_check=False)          | Full Sync (full_check=True)                         |
|:------------------------ |:-------------------------------------- |:--------------------------------------------------- |
| New Workouts             | Scrapes names and downloads TCX.       | Scrapes names and downloads TCX.                    |
| Metadata Updates         | Ignored. Existing CSV data is trusted. | Updated. Refreshes Calories, Pace, and Stats.       |
| Missing File Recovery    | Ignored.                               | Active. Re-downloads missing TCX files.             |
| Fingerprint Healing      | Ignored.                               | Active. Re-syncs fingerprints to match GPS data.    |
| Filename Standardization | Ignored.                               | Active. Renames generic files if name is recovered. |
| Runtime Name Recovery    | Limited. Recent batch only.            | Total. Refreshes names/paths for full history.      |

- Sync from Local CSV: Offline mode. Processes mapmyride_export.csv if manually downloaded. 
- Generate Maps: Re-processes simplified tracks and rebuilds the all_routes.html interactive map. By default, this filters for walks and hikes.