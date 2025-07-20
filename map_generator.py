# map_generator.py

import configparser
from pathlib import Path
from typing import List, Tuple

import folium
import geopandas as gpd
import lxml.etree as ET
from shapely.geometry import LineString

# Import the new Workout class, as this generator will operate on Workout objects
from workout import Workout


# --- Geospatial Helper Functions ---

def _parse_tcx_for_coords(tcx_file: str) -> List[Tuple[float, float]]:
    """
    Parses a TCX file and extracts a list of (longitude, latitude) coordinates.
    This is a focused helper specifically for geospatial processing.
    """
    try:
        tree = ET.parse(tcx_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Error parsing XML in file: {tcx_file}. Reason: {e}")
        return []

    # Define the namespace map. This is more robust than hardcoding 'ns'.
    ns = {'tcx': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    coordinates = []

    for trackpoint in root.findall('.//tcx:Trackpoint', ns):
        position = trackpoint.find('tcx:Position', ns)
        if position is not None:
            try:
                lat_el = position.find('tcx:LatitudeDegrees', ns)
                lon_el = position.find('tcx:LongitudeDegrees', ns)
                if lat_el is not None and lon_el is not None:
                    lat = float(lat_el.text)
                    lon = float(lon_el.text)
                    coordinates.append((lon, lat))
            except (AttributeError, ValueError, TypeError) as e:
                print(f"Skipping malformed trackpoint in {Path(tcx_file).name}. Reason: {e}")
                continue

    return coordinates


def _create_simplified_geojson(
        tcx_path: Path,
        geojson_path: Path,
        tolerance: float = 10.0
):
    """
    Creates a simplified GeoJSON file from a TCX file using GeoPandas.
    (This is the refactored version of the original function)
    """
    try:
        coordinates = _parse_tcx_for_coords(str(tcx_path))
        if len(coordinates) < 2:
            print(f"  - Skipping {tcx_path.name}: not enough points to form a line.")
            return

        line = LineString(coordinates)
        # Create a GeoDataFrame with the correct CRS for geographic coordinates
        gdf = gpd.GeoDataFrame([1], geometry=[line], crs="EPSG:4326")

        # To use a tolerance in meters, we need to project to a suitable CRS.
        # We can find the appropriate UTM zone from the centroid.
        utm_crs = gdf.estimate_utm_crs(datum_name="WGS 84")

        # Project, simplify, and then project back to the original CRS
        gdf_projected = gdf.to_crs(utm_crs)
        gdf_projected['geometry'] = gdf_projected.geometry.simplify(
            tolerance=tolerance, preserve_topology=True
        )
        gdf_simplified = gdf_projected.to_crs(gdf.crs)

        # Save the simplified geometry to a GeoJSON file
        gdf_simplified.to_file(str(geojson_path), driver='GeoJSON')
        print(f"  - Simplified '{tcx_path.name}' -> '{geojson_path.name}'")

    except Exception as e:
        print(f"  - ❌ ERROR simplifying '{tcx_path.name}': {e}")


# --- The main MapGenerator class ---

class MapGenerator:
    """
    Handles the creation of visual map outputs from workout data.
    """

    def __init__(self, config: configparser.ConfigParser):
        """
        Initializes the map generator with paths from the configuration.

        Args:
            config: The application configuration object.
        """
        self.simplified_folder = Path(config.get('paths', 'simplified_gps_track_folder'))
        self.project_path = Path(config.get('paths', 'project_path'))
        self.map_file_path = self.project_path / "all_routes.html"

        # Ensure the destination directory exists
        self.simplified_folder.mkdir(parents=True, exist_ok=True)

    def simplify_workouts(self, workouts: List[Workout], only_if_missing: bool = True):
        """
        Creates simplified GeoJSON files for a list of workouts.

        Args:
            workouts: A list of Workout objects to process.
            only_if_missing: If True, only creates a GeoJSON file if one
                             does not already exist.
        """
        if only_if_missing:
            print("\n--- Simplifying TCX files (Incremental Mode) ---")
        else:
            print("\n--- Simplifying TCX files (Full Rebuild Mode) ---")

        created_count = 0
        skipped_count = 0
        error_count = 0

        # Filter for workouts that should be on the map
        walk_hike_workouts = [w for w in workouts if w.activity_type in ('Walk', 'Hike')]
        print(f"Found {len(walk_hike_workouts)} Walk/Hike workouts to process for simplification.")

        for workout in walk_hike_workouts:
            if not workout.tcx_path:
                continue

            dest_path = self.simplified_folder / (workout.tcx_path.stem + '.geojson')

            if not only_if_missing or not dest_path.exists():
                if workout.tcx_path.exists():
                    _create_simplified_geojson(
                        tcx_path=workout.tcx_path,
                        geojson_path=dest_path,
                        tolerance=10.0
                    )
                    created_count += 1
                else:
                    print(f"  - ⚠️ WARNING: Source file not found, cannot simplify: {workout.tcx_path}")
                    error_count += 1
            else:
                skipped_count += 1

        print("\n--- Simplification Summary ---")
        print(f"GeoJSON files created/updated: {created_count}")
        print(f"Files skipped (already exist): {skipped_count}")
        if error_count > 0:
            print(f"Errors (source file not found): {error_count}")

    def create_route_map(self):
        """
        Creates an HTML map visualizing all simplified GeoJSON routes.
        """
        print("\n--- Creating HTML Route Map ---")

        geojson_files = list(self.simplified_folder.glob("*.geojson"))
        if not geojson_files:
            print("No GeoJSON files found to map. Please run simplification first.")
            return

        print(f"Found {len(geojson_files)} GeoJSON files to add to the map.")

        # Create a map centered on a default location, which will be auto-adjusted.
        m = folium.Map(location=[-31.95, 115.86], zoom_start=10)
        feature_group = folium.FeatureGroup(name="All Routes")

        for geojson_file in geojson_files:
            try:
                folium.GeoJson(
                    str(geojson_file),
                    style_function=lambda x: {'color': 'blue', 'weight': 2.5, 'opacity': 0.7}
                ).add_to(feature_group)
            except Exception as e:
                print(f"  - Could not process GeoJSON file '{geojson_file.name}': {e}")

        feature_group.add_to(m)
        m.fit_bounds(feature_group.get_bounds())

        try:
            m.save(str(self.map_file_path))
            print(f"\n✅ Successfully created map: '{self.map_file_path}'")
        except Exception as e:
            print(f"\n❌ ERROR: Could not save the map file. Reason: {e}")
