# map_generator.py

import configparser
import logging
from pathlib import Path
from typing import List, Tuple

import folium
import geopandas as gpd
import lxml.etree as ET
from shapely.geometry import LineString

# Import the new Workout class, as this generator will operate on Workout objects
from workout import Workout

# It's best practice to get the logger at the module level
log = logging.getLogger(__name__)


# --- Geospatial Helper Functions ---

def _parse_tcx_for_coords(tcx_file: str) -> List[Tuple[float, float]]:
    """
    Parses a TCX file and extracts a list of (longitude, latitude) coordinates.
    This version uses a robust method to handle XML namespaces correctly.
    """
    try:
        with open(tcx_file, 'rb') as f:
            parser = ET.XMLParser(remove_blank_text=True)
            tree = ET.parse(f, parser)
        root = tree.getroot()
    except Exception as e:
        log.error(f"Failed at initial parsing stage for {Path(tcx_file).name}. Reason: {e}")
        return []

    # --- Namespace Handling ---
    ns = root.nsmap
    if None in ns:
        ns['default'] = ns.pop(None)

    # --- Find Trackpoints ---
    try:
        trackpoints = root.findall('.//default:Trackpoint', namespaces=ns)
        if not trackpoints:
            trackpoints = root.findall('.//Trackpoint') # Fallback
    except Exception as e:
        log.error(f"Failed during findall operation for trackpoints. Reason: {e}")
        return []

    if not trackpoints:
        log.warning(f"No <Trackpoint> elements found in {Path(tcx_file).name}. The file may be empty or structured unexpectedly.")
        return []

    # --- Extract Coordinates ---
    coordinates = []
    for i, trackpoint in enumerate(trackpoints):
        position = trackpoint.find('default:Position', namespaces=ns)
        if position is None:
            position = trackpoint.find('Position') # Fallback

        if position is not None:
            lat_el = position.find('default:LatitudeDegrees', namespaces=ns)
            if lat_el is None: lat_el = position.find('LatitudeDegrees') # Fallback

            lon_el = position.find('default:LongitudeDegrees', namespaces=ns)
            if lon_el is None: lon_el = position.find('LongitudeDegrees') # Fallback

            if lat_el is not None and lon_el is not None and lat_el.text is not None and lon_el.text is not None:
                try:
                    lat = float(lat_el.text)
                    lon = float(lon_el.text)
                    coordinates.append((lon, lat))
                except (ValueError, TypeError):
                    log.warning(f"Skipping malformed coordinate text in trackpoint {i} in {Path(tcx_file).name}")

    log.info(f"Extracted {len(coordinates)} coordinates from {len(trackpoints)} trackpoints in {Path(tcx_file).name}.")
    return coordinates


def _create_simplified_geojson(
        tcx_path: Path,
        geojson_path: Path,
        tolerance: float = 10.0
):
    """
    Creates a simplified GeoJSON file from a TCX file using GeoPandas.
    """
    try:
        coordinates = _parse_tcx_for_coords(str(tcx_path))
        if len(coordinates) < 2:
            log.warning(f"Skipping {tcx_path.name}: not enough points ({len(coordinates)}) to form a line.")
            return

        line = LineString(coordinates)
        gdf = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326")

        # Simplify geometry
        utm_crs = gdf.estimate_utm_crs(datum_name="WGS 84")
        gdf_projected = gdf.to_crs(utm_crs)
        gdf_projected['geometry'] = gdf_projected.geometry.simplify(
            tolerance=tolerance, preserve_topology=True
        )
        gdf_simplified = gdf_projected.to_crs(gdf.crs)

        if gdf_simplified.empty or gdf_simplified.geometry.is_empty.all():
            log.warning(f"Geometry for {tcx_path.name} became empty after simplification (tolerance={tolerance}).")
            return

        gdf_simplified.to_file(str(geojson_path), driver='GeoJSON')
        log.info(f"Simplified '{tcx_path.name}' -> '{geojson_path.name}'")

    except Exception as e:
        log.error(f"ERROR simplifying '{tcx_path.name}': {e}", exc_info=True)


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
                    _create_simplified_geojson(
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