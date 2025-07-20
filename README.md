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

## Setup

1.  **Clone the repository:**
2.  **Create a Virtual Environment (Recommended):** 
3.  **Install Dependencies:** This project's dependencies are listed in `requirements.txt`.
    