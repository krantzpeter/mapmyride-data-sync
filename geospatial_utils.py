# geospatial_utils.py

import logging
from pathlib import Path
from typing import List, Tuple

import geopandas as gpd
import lxml.etree as ET
from shapely.geometry import LineString

log = logging.getLogger(__name__)


def parse_tcx_for_coords(tcx_file: str) -> List[Tuple[float, float]]:
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


def create_simplified_geojson(
        tcx_path: Path,
        geojson_path: Path,
        tolerance: float = 10.0
):
    """
    Creates a simplified GeoJSON file from a TCX file using GeoPandas.
    """
    try:
        coordinates = parse_tcx_for_coords(str(tcx_path))
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
