# map_generator.py

import configparser
import logging
from pathlib import Path
from typing import List

import folium

# Import the new Workout class, as this generator will operate on Workout objects
from workout import Workout
# Import the refactored geospatial helper function
from geospatial_utils import create_simplified_geojson

# It's best practice to get the logger at the module level
log = logging.getLogger(__name__)


# --- The main MapGenerator class ---

class MapGenerator:
    """
    Handles the creation of visual map outputs from workout data.
    """

    def __init__(self, config: configparser.ConfigParser):
        """
        Initializes the map generator with paths from the configuration.
        """
        self.simplified_folder = Path(config.get('paths', 'simplified_gps_track_folder'))
        self.project_path = Path(config.get('paths', 'project_path'))
        self.map_file_path = self.project_path / "all_routes.html"
        self.simplified_folder.mkdir(parents=True, exist_ok=True)
        log.info(f"MapGenerator initialized. Simplified tracks in: {self.simplified_folder}")

    def simplify_workouts(self, workouts: List[Workout], workout_types: set = None, only_if_missing: bool = True):
        """
        Creates simplified GeoJSON files for a list of workouts.
        """
        mode = "Incremental Mode" if only_if_missing else "Full Rebuild Mode"
        log.info(f"--- Simplifying TCX files ({mode}) ---")

        workouts_to_process = [w for w in workouts if not workout_types or w.activity_type.lower() in workout_types]
        log.info(f"Found {len(workouts_to_process)} workouts of specified types to process for simplification.")

        for workout in workouts_to_process:
            if not workout.tcx_path:
                continue

            dest_path = self.simplified_folder / (workout.tcx_path.stem + '.geojson')

            if not only_if_missing or not dest_path.exists():
                if workout.tcx_path.exists():
                    # Call the refactored helper function
                    create_simplified_geojson(
                        tcx_path=workout.tcx_path,
                        geojson_path=dest_path,
                        tolerance=10.0
                    )
                else:
                    log.warning(f"Source file not found, cannot simplify: {workout.tcx_path}")

    def create_route_map(self):
        """
        Creates an HTML map visualizing all simplified GeoJSON routes.
        """
        log.info("--- Creating HTML Route Map ---")
        geojson_files = list(self.simplified_folder.glob("*.geojson"))
        if not geojson_files:
            log.warning("No GeoJSON files found to map. Please run simplification first.")
            return

        log.info(f"Found {len(geojson_files)} GeoJSON files to add to the map.")

        # Center the map on Perth, WA as a default
        m = folium.Map(location=[-31.95, 115.86], zoom_start=10)
        feature_group = folium.FeatureGroup(name="All Routes")

        for geojson_file in geojson_files:
            try:
                popup_text = geojson_file.stem
                folium.GeoJson(
                    str(geojson_file),
                    style_function=lambda x: {'color': 'blue', 'weight': 2.5, 'opacity': 0.7},
                    popup=folium.Popup(popup_text)
                ).add_to(feature_group)
            except Exception as e:
                log.error(f"Could not process GeoJSON file '{geojson_file.name}': {e}")

        feature_group.add_to(m)

        # Auto-fit the map to the bounds of the routes
        if feature_group.get_bounds():
            m.fit_bounds(feature_group.get_bounds())

        try:
            m.save(str(self.map_file_path))
            log.info(f"Successfully created map: '{self.map_file_path}'")
        except Exception as e:
            log.error(f"Could not save the map file. Reason: {e}")
