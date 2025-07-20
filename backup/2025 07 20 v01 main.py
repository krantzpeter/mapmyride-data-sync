# import selenium.webdriver
from tcx2gpx.tcx2gpx import TCX2GPX
import folium
import csv
import geopandas as gpd
from shapely.geometry import LineString
from pathlib import Path
import re

import time
import os
import shutil
import configparser
import lxml.etree as ET  # Use lxml and alias it as ET for consistency
import geojson

from typing import List, Tuple, Set, Dict, Optional
# from selenium.webdriver import Chrome
from selenium.webdriver.chrome.options import Options as ChromeOptions
# from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Keys

from datetime import datetime

def parse_tcx(tcx_file: str, seconds_between_points: float = 0.0):
    """
    Parses a TCX (Training Center XML) file and extracts geographical points.

    This function reads a TCX file, extracts the track points, and returns a list of coordinate tuples.
    The points are filtered based on the specified time interval between consecutive points.

    Parameters:
    tcx_file (str): The path to the TCX file to be parsed.
    seconds_between_points (float): The minimum time interval in seconds between consecutive points.
                                    The first valid point is always added.

    Returns:
    list: A list of coordinate tuples representing the geographical points.
    """
    # Parse the TCX file and get the root element
    tree = ET.parse(tcx_file)
    root = tree.getroot()
    ns = {'ns': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}

    # Define the time format used in the TCX file
    time_format = "%Y-%m-%dT%H:%M:%S.%f%z"

    coordinates = []
    start_time = None

    # Iterate over all Trackpoint elements in the TCX file
    for trackpoint in root.findall('.//ns:Trackpoint', ns):
        # Find the Time and Position elements within the Trackpoint
        time_element = trackpoint.find('ns:Time', ns)
        position = trackpoint.find('ns:Position', ns)

        # Ensure both Time and Position elements are present
        if time_element is not None and position is not None:
            try:
                # Parse the time string into a datetime object
                this_time = datetime.strptime(time_element.text, time_format)
            except ValueError as e:
                print(f"Error parsing time: {e}")
                continue

            # Always add the first valid position
            if start_time is None:
                start_time = this_time

                try:
                    lat = float(position.find('ns:LatitudeDegrees', ns).text)
                    lon = float(position.find('ns:LongitudeDegrees', ns).text)
                    coordinates.append((lon, lat))
                except (AttributeError, ValueError) as e:
                    print(f"Error parsing position: {e}")
                    continue
            else:
                # Check if the elapsed time is greater than the threshold
                elapsed_time_in_seconds = (this_time - start_time).total_seconds()
                if elapsed_time_in_seconds > seconds_between_points:
                    start_time = this_time

                    try:
                        lat = float(position.find('ns:LatitudeDegrees', ns).text)
                        lon = float(position.find('ns:LongitudeDegrees', ns).text)
                        coordinates.append((lon, lat))
                    except (AttributeError, ValueError) as e:
                        print(f"Error parsing position: {e}")
                        continue

    return coordinates


def convert_tcx_to_geojson(tcx_file, geojson_file):
    coordinates = parse_tcx(tcx_file, seconds_between_points=20)
    line_string = geojson.LineString(coordinates)
    feature = geojson.Feature(geometry=line_string)
    feature_collection = geojson.FeatureCollection([feature])

    with open(geojson_file, 'w') as f:
        geojson.dump(feature_collection, f)


def read_gpx_from_tcx(tcx_filename):
    """
    Reads a tcx file, converts it to a gpx file and returns a fcx2gpx gps_object
    :param tcx_filename:
    :return:
    """
    gps_object = TCX2GPX(tcx_path=tcx_filename)
    gps_object.extract_track_points()
    return gps_object


def read_known_tcx_file_csv(tcx_file_list: str) -> list[dict[str, str]]:
    """
    Reads a list of known TCX files and their attributes from a CSV filename that has been previously
    created for a folder by update_known_tcx_file_csv() and updated by export_tcx_files()
    :param tcx_file_list:       Filespec for CSV file to read list of TCX files and their attributes
    :return:                    Returns a list of dict, one for each file.  Each dict contains
                                str values for the following:
                                'Id', 'Activity', 'DistanceMeters', 'TotalTimeSeconds', 'LatitudeDegrees',
                                'LongitudeDegrees', 'Filename', 'Fingerprint'
    """
    known_workouts: list[dict[str, str]] = []
    workout: dict
    with open(tcx_file_list, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for workout in reader:
            known_workouts.append(workout)

    return known_workouts


def write_tcx_file_prop_list_to_csv(row_dicts: list[dict[str, str]], dest_tcx_file_list: str) -> None:
    """
    Creates a CSV file with the properties of the specified TCX file list attributes
    :param row_dicts:               list of dicts, each of which contains str attributes for
                                    'Id', 'Activity', 'DistanceMeters', 'TotalTimeSeconds', 'LatitudeDegrees',
                                    'LongitudeDegrees', 'Filename', 'Fingerprint'
    :param dest_tcx_file_list: str  Filespec for CSV file to create with list of TCX files and their attributes
    :return:
    """
    with open(dest_tcx_file_list, 'w', encoding='UTF8', newline='') as f:
        writer = csv.writer(f)
        # write heading row
        writer.writerow(
            ['Filename', 'Id', 'Activity Type', 'Distance (m)', 'Workout Time (seconds)', 'Latitude Degrees',
             'Longitude Degrees', 'PK TCX Fingerprint'])
        for row_dict in row_dicts:
            row = (row_dict['Filename'],
                   row_dict['Id'],
                   row_dict['Activity'],
                   row_dict['DistanceMeters'],
                   row_dict['TotalTimeSeconds'],
                   row_dict['LatitudeDegrees'],
                   row_dict['LongitudeDegrees'],
                   row_dict['Fingerprint']
                   )

            writer.writerow(row)


def remove_duplicate_tcx_files_in_folder(source_folder: str, list_dont_delete: bool = False) -> list[dict[str, str]]:
    """
    Deletes any duplicate TCX files in a folder, leaving only one unique copy of each file.
    This version uses the robust Duration+Distance fingerprinting scheme and is self-contained.

    Args:
        source_folder: Filespec of source folder to search for TCX files.
        list_dont_delete: True if duplicates should be listed but not deleted.

    Returns:
        A list of dictionaries, one for each unique file, containing its properties.
    """
    print(f"\n--- Scanning for Duplicates in '{source_folder}' ---")
    source_path = Path(source_folder)

    # Step 1: Scan all TCX files and generate authoritative fingerprints.
    fingerprints_to_files: Dict[str, List[Path]] = {}
    all_tcx_files = list(source_path.glob("*.tcx"))
    print(f"Found {len(all_tcx_files)} TCX files to process...")

    for tcx_path in all_tcx_files:
        fingerprint = get_fingerprint_from_tcx_file(tcx_path)
        if fingerprint:
            if fingerprint not in fingerprints_to_files:
                fingerprints_to_files[fingerprint] = []
            fingerprints_to_files[fingerprint].append(tcx_path)

    # Step 2: Identify duplicates and decide which to keep/delete.
    files_to_delete: List[Path] = []
    unique_files: Set[Path] = set()

    for fingerprint, file_list in fingerprints_to_files.items():
        if len(file_list) > 1:
            # Sort by name to make the choice of which to keep deterministic
            file_list.sort()
            file_to_keep = file_list[0]
            unique_files.add(file_to_keep)
            print(f"  -> Found duplicates for fingerprint '{fingerprint}'. Keeping '{file_to_keep.name}'.")

            # Add the rest of the files in the list to the deletion queue
            files_to_delete.extend(file_list[1:])
        else:
            # This file is already unique
            unique_files.add(file_list[0])

    # Step 3: Perform the deletion if requested.
    if files_to_delete:
        print(f"Found {len(files_to_delete)} duplicate files to handle.")
        for file_path in files_to_delete:
            if list_dont_delete:
                print(f"     - Would delete duplicate: '{file_path.name}'")
            else:
                print(f"     - Deleting duplicate: '{file_path.name}'")
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"     - ERROR: Could not delete file: {e}")
    else:
        print("No duplicate files found.")

    # Step 4: Build the list of property dictionaries for the remaining unique files.
    # This maintains the original function's return type for consistency.
    final_rows = []
    print("\nGenerating property list for unique files...")
    for unique_path in sorted(list(unique_files)):  # sort for deterministic output
        try:
            props = extract_tcx_file_properties(str(unique_path))
            # Add the Filename and Fingerprint to the properties dictionary
            props['Filename'] = str(unique_path)
            props['Fingerprint'] = get_fingerprint_from_tcx_file(unique_path)
            final_rows.append(props)
        except Exception as e:
            print(f"Could not process properties for file '{unique_path.name}': {e}")

    return final_rows


def get_fingerprint_from_tcx_file(tcx_filepath: Path, silent: bool = False) -> Optional[str]:
    """
    Calculates an authoritative fingerprint directly from a TCX file's properties
    using the new Duration + Distance scheme.

    This function serves as the single source of truth for generating a fingerprint
    from a file on disk.

    Args:
        tcx_filepath: The path to the TCX file (as a Path object).
        silent: If True, suppresses the printing of diagnostic data.

    Returns:
        The calculated fingerprint string, or None if the file cannot be
        processed or lacks the required data.
    """
    if not tcx_filepath.exists():
        if not silent:
            print(f"ERROR: File not found at {tcx_filepath}")
        return None

    try:
        props = extract_tcx_file_properties(str(tcx_filepath))
        time_seconds_str = props.get('TotalTimeSeconds', '0')
        distance_meters_str = props.get('DistanceMeters', '0')

        if not silent:
            # Add this for clearer diagnostic output in the test function
            print(f"      - Time (sec):   {time_seconds_str}")
            print(f"      - Dist (m):     {distance_meters_str}")

        fingerprint = create_tcx_fingerprint_from_data(
            total_time_seconds=time_seconds_str or '0',
            distance_m=distance_meters_str or '0'
        )
        return fingerprint

    except Exception as e:
        if not silent:
            print(f"ERROR: Could not generate fingerprint for '{tcx_filepath.name}'. Reason: {e}")
        return None


def create_tcx_fingerprint_from_data(total_time_seconds: str, distance_km: str = '0', distance_m: str = '0') -> str:
    """
    Creates a unique, robust fingerprint based on workout duration and distance.

    The fingerprint format is T<seconds_padded>D<centimeters_padded>.
    This version now uses distance with centimeter precision (equivalent to 5 decimal
    places for kilometers) for higher uniqueness, as requested for identification purposes.

    Args:
        total_time_seconds: The total duration of the workout in seconds.
        distance_km: The distance in kilometers (typically from the online CSV).
        distance_m: The distance in meters (typically from the TCX file).

    Returns:
        The generated fingerprint string.
    """
    try:
        # Round to nearest second to handle floating point variations
        time_sec = int(round(float(total_time_seconds or 0)))
    except (ValueError, TypeError):
        time_sec = 0

    dist_cm = 0
    # Prioritize the more precise 'meters' value if available (from TCX)
    if distance_m and float(distance_m or 0) > 0:
        try:
            # Convert meters to centimeters and round to the nearest integer
            dist_cm = int(round(float(distance_m) * 100))
        except (ValueError, TypeError):
            dist_cm = 0
    # Otherwise, use the 'kilometers' value (from CSV)
    elif distance_km:
        try:
            # Convert km to centimeters (km * 1000 * 100) and round
            dist_cm = int(round(float(distance_km) * 100000))
        except (ValueError, TypeError):
            dist_cm = 0

    # Use 10-digit padding for the distance in centimeters to be safe for long workouts.
    return f"T{time_sec:08d}D{dist_cm:010d}"


def test_fingerprint_matching_detailed(config: configparser.ConfigParser, limit: int = 10):
    """
    Performs a detailed, side-by-side comparison for a limited number of workouts
    using the new Duration + Distance fingerprinting scheme.

    This function is a diagnostic tool. For each of the first 'limit' workouts
    in the local `mapmyride_export.csv`, it:
    1.  Displays the raw duration/distance data used to generate the "candidate" fingerprint.
    2.  Downloads the actual TCX file for that workout.
    3.  Displays the raw duration/distance data from inside the TCX file.
    4.  Generates both fingerprints and prints them side-by-side for comparison.
    5.  States clearly whether they match or mismatch.

    This requires an active internet connection and a browser session to download
    the TCX files for the comparison.

    Args:
        config: The application configuration object.
        limit: The number of workouts from the top of the CSV to test.
    """
    print(f"\n--- DETAILED FINGERPRINT COMPARISON (first {limit} workouts) ---")

    # 1. Get config and setup paths
    local_csv_path = Path(config.get('debugging', 'local_csv_path'))
    tcx_export_url_template = config.get('urls', 'tcx_export_url_template')
    temp_download_dir = get_downloads_folder() / f"sel_map_extract_temp_{int(time.time())}"

    if not local_csv_path.exists():
        print(f"ERROR: Local CSV not found at {local_csv_path}. Please run an online sync first to create it.")
        return

    # 2. Read the workouts from the local CSV
    with open(local_csv_path, 'r', newline='', encoding='utf-8') as f:
        workouts = list(csv.DictReader(f))

    # 3. Setup browser and perform comparison
    try:
        temp_download_dir.mkdir(parents=True, exist_ok=True)
        chrome_options = ChromeOptions()
        prefs = {"download.default_directory": str(temp_download_dir)}
        chrome_options.add_experimental_option("prefs", prefs)

        with webdriver.Chrome(options=chrome_options) as browser:
            # Must log in to download TCX files
            print("Login required for diagnostic downloads...")
            if not _login_to_mapmyride(browser, config):
                print("FATAL: Login failed. Cannot run detailed comparison.")
                return

            # 4. Loop through the limited set of workouts
            for i, workout_data in enumerate(workouts[:limit]):
                print(f"\n----- Comparing Workout {i + 1}/{limit} -----")

                # --- A. Get Candidate Data from CSV ---
                print("  [1] Data from Online CSV:")
                candidate_time = workout_data.get('Workout Time (seconds)', '0')
                candidate_dist_km = workout_data.get('Distance (km)', '0')
                print(f"      - Time (sec):   {candidate_time}")
                print(f"      - Dist (km):    {candidate_dist_km}")

                try:
                    # Correctly call with time and distance_km
                    candidate_fp = create_tcx_fingerprint_from_data(
                        total_time_seconds=candidate_time,
                        distance_km=candidate_dist_km
                    )
                    print(f"      => Candidate FP: {candidate_fp}")
                except (ValueError, TypeError) as e:
                    candidate_fp = f"ERROR: {e}"
                    print(f"      => Candidate FP: {candidate_fp}")

                # --- B. Get Authoritative Data from TCX ---
                print("  [2] Data from Downloaded TCX File:")
                workout_id = _get_workout_id_from_link(workout_data.get('Link', ''))
                if not workout_id:
                    print("      - ERROR: Could not find workout ID in link.")
                    continue

                downloaded_path = _download_file_and_wait(browser,
                                                          tcx_export_url_template.format(workout_id=workout_id),
                                                          temp_download_dir)

                if not downloaded_path:
                    print(f"      - ERROR: Failed to download TCX for workout ID {workout_id}")
                    continue

                authoritative_fp = "ERROR: Could not be generated"
                try:
                    # The get_fingerprint_from_tcx_file function will now print the raw data
                    authoritative_fp = get_fingerprint_from_tcx_file(downloaded_path)
                    print(f"      => Authoritative FP: {authoritative_fp}")
                except Exception as e:
                    authoritative_fp = f"ERROR: {e}"
                    print(f"      => Authoritative FP: {authoritative_fp}")

                # --- C. Compare and Conclude ---
                print("  [3] Result:")
                if "ERROR" in str(candidate_fp) or "ERROR" in str(authoritative_fp):
                    print("      - COMPARISON SKIPPED due to error.")
                elif candidate_fp == authoritative_fp:
                    print("      - ✅ MATCH")
                else:
                    print("      - ❌ MISMATCH")

                # Clean up the downloaded file
                try:
                    os.remove(downloaded_path)
                except OSError:
                    pass
    finally:
        # Clean up temp dir
        if temp_download_dir.exists():
            shutil.rmtree(temp_download_dir)
            print(f"\nCleaned up temporary directory: {temp_download_dir}")


def extract_tcx_file_properties(tcx_filename: str) -> Dict[str, str]:
    """
    Reads a TCX file and extracts specified properties into a Dictionary.
    This version is now robust and can handle TCX files with or without a
    default XML namespace, preventing crashes on non-standard files.

    Known attributes (all str values):
        Activity            - Type of workout activity - e.g. Running
        Id                  - The workout start time in GMT (e.g., 2022-08-25T22:51:40+00:00)
        StartTime           - The lap start time in GMT (e.g., 2022-08-25T22:51:40+00:00)
        TotalTimeSeconds    - Total time in seconds of workout  - e.g. 821.0
        DistanceMeters      - Distance of the workout in meters - e.g. 2008.1072563199998
        LatitudeDegrees     - Latitude of start of workout in decimal degrees - e.g. -31.96620216262969
        LongitudeDegrees    - Longitude of start of workout in decimal degrees - e.g. -31.96620216262969
    :param tcx_filename: str with fully qualified path to tcx file
    :return: Dictionary {<attribute name str>, <value str>}
    """
    prop_dict = {}
    try:
        tree = ET.parse(tcx_filename)
        root = tree.getroot()

        # Safely get the default namespace URI if it exists.
        # If not, ns_uri will be an empty string, and the code will still work.
        ns_uri = root.nsmap.get(None, '')
        ns = f'{{{ns_uri}}}' if ns_uri else ''

        # Find the Activity element using the correctly formatted namespace.
        activity_el = root.find(f'.//{ns}Activity')
        if activity_el is not None:
            prop_dict['Activity'] = activity_el.get('Sport')
            lap_el = activity_el.find(f'{ns}Lap')
            if lap_el is not None:
                prop_dict['StartTime'] = lap_el.get('StartTime')

        # Find other attributes using the same robust namespace formatting.
        attribs = ('Id', 'TotalTimeSeconds', 'DistanceMeters', 'LatitudeDegrees', 'LongitudeDegrees')
        for attrib in attribs:
            el = root.find(f'.//{ns}{attrib}')
            if el is not None:
                prop_dict[attrib] = el.text

    except ET.ParseError as e:
        print(f"ERROR: Could not parse TCX file '{os.path.basename(tcx_filename)}'. Reason: {e}")
        return {}  # Return an empty dict on failure

    return prop_dict


def _get_workout_id_from_link(link: str) -> str:
    """
    Extracts the unique workout ID from a MapMyRide workout URL.
    Example: 'https://www.mapmyride.com/workout/12345' -> '12345'

    Args:
        link: The full workout URL from the CSV file.

    Returns:
        The extracted workout ID as a string, or an empty string if the link is invalid.
    """
    if not link:
        return ''
    return link.strip('/').split('/')[-1]


def _parse_csv_date_str(date_str: str) -> Optional[datetime]:
    """
    Robustly parses a date string from the MapMyRide CSV into a datetime object.

    This is the single source of truth for parsing date strings from the CSV.
    It handles formats with full month names ('September') and abbreviated
    month names ('Sept.').

    Args:
        date_str: The date string from the workout data (e.g., "Sept. 12, 2023").

    Returns:
        A datetime object if parsing is successful, otherwise None.
    """
    if not date_str:
        return None

    # Handle the non-standard 'Sept.' abbreviation before general cleaning
    if 'Sept.' in date_str:
        date_str = date_str.replace('Sept.', 'Sep.')

    # Clean the string by removing the period from other abbreviated months (e.g., 'Jan.')
    cleaned_date_str = date_str.replace('.', '')
    # Attempt to parse with full month name first, then abbreviated
    for fmt in ('%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(cleaned_date_str, fmt)
        except ValueError:
            continue

    # Return None if all known formats fail
    return None


def _format_date_for_filename(date_str: str) -> str:
    """
    Parses a date string from the CSV and returns a 'YYYY MM DD' string.
    This function now uses the centralized _parse_csv_date_str helper.

    Args:
        date_str: The date string from the workout data.

    Returns:
        A formatted date string, e.g., "2023 09 12".
    """
    dt = _parse_csv_date_str(date_str)
    if dt:
        return dt.strftime('%Y %m %d')

    # Fallback for unparseable or empty dates
    print(f"WARNING: Could not parse date '{date_str}'. Using today's date for filename.")
    return datetime.now().strftime('%Y %m %d')


def _download_file_and_wait(browser: webdriver.Chrome, url: str, download_dir: Path) -> Optional[Path]:
    """
    Navigates to a URL to trigger a download and waits for the file to appear.

    This is a robust way to handle downloads by monitoring a specific directory
    for a new file, avoiding race conditions with other system downloads.

    Args:
        browser: The active Selenium Chrome WebDriver instance.
        url: The URL that triggers the file download.
        download_dir: The specific directory (as a Path object) to monitor for the new file.

    Returns:
        The full path to the newly downloaded file as a Path object, or None if it fails.
    """
    # Get the set of files in the directory *before* starting the download
    files_before: Set[str] = {p.name for p in download_dir.iterdir()}

    browser.get(url)

    # Poll the directory until a new, completely downloaded file appears
    for _ in range(60):  # Wait up to 30 seconds (60 * 0.5s)
        files_after: Set[str] = {p.name for p in download_dir.iterdir()}
        new_files: Set[str] = files_after - files_before

        if new_files:
            new_file_name = new_files.pop()
            # Ensure the file is not a temporary download file (.tmp or .crdownload)
            if not new_file_name.endswith(('.tmp', '.crdownload')):
                return download_dir / new_file_name
        time.sleep(0.5)

    print(f"Download timed out. Failed to download file from URL: {url}")
    return None


def _get_unique_filepath(directory: Path, filename: str) -> Path:
    """
    Finds a unique filepath in a directory, adding a suffix if the file exists.

    Checks if a file exists at the target path. If it does, it appends a
    suffix like '_0001', '_0002', etc., until an unused path is found.

    Args:
        directory: The target directory (as a Path object).
        filename: The desired name of the file.

    Returns:
        A unique Path object for the file in the target directory.
    """
    filepath = directory / filename
    if not filepath.exists():
        return filepath

    # If the file exists, start finding a unique name
    stem = filepath.stem
    suffix = filepath.suffix
    counter = 1
    while filepath.exists():
        new_filename = f"{stem}_{counter:04d}{suffix}"
        filepath = directory / new_filename
        counter += 1
    return filepath


def _bootstrap_log_from_folder(source_folder: Path, log_path: Path) -> Dict[str, Path]:
    """
    Creates a new download log by scanning all TCX files in a folder.

    This function handles the one-time bootstrap process. It finds all files,
    generates authoritative fingerprints, removes any local file duplicates,
    and writes the clean list to the log file.

    Args:
        source_folder: The directory where TCX files are stored.
        log_path: The path where the new log file should be created.

    Returns:
        A dictionary mapping each unique fingerprint to its corresponding file Path.
    """
    print(f"INFO: Download log '{log_path.name}' not found. Bootstrapping by scanning '{source_folder}'...")
    fingerprint_map: Dict[str, Path] = {}
    try:
        # Step 1: Scan all TCX files and generate authoritative fingerprints for each.
        fingerprints_to_files: Dict[str, List[Path]] = {}
        all_tcx_files = list(source_folder.glob("*.tcx"))
        print(f"Found {len(all_tcx_files)} TCX files to process...")

        for tcx_path in all_tcx_files:
            fingerprint = get_fingerprint_from_tcx_file(tcx_path)
            if fingerprint:
                if fingerprint not in fingerprints_to_files:
                    fingerprints_to_files[fingerprint] = []
                fingerprints_to_files[fingerprint].append(tcx_path)

        # Step 2: Identify and remove duplicates, keeping the first file found.
        log_rows = []
        for fingerprint, file_list in fingerprints_to_files.items():
            file_list.sort()  # Sort to make the choice of which to keep deterministic
            file_to_keep = file_list[0]
            fingerprint_map[fingerprint] = file_to_keep
            log_rows.append({'Fingerprint': fingerprint, 'Filename': str(file_to_keep)})

            if len(file_list) > 1:
                print(f"  -> Found duplicates for fingerprint '{fingerprint}'. Keeping '{file_to_keep.name}'.")
                for file_to_delete in file_list[1:]:
                    print(f"     - Deleting duplicate: '{file_to_delete.name}'")
                    try:
                        os.remove(file_to_delete)
                    except OSError as e:
                        print(f"     - ERROR: Could not delete file: {e}")

        # Step 3: Create the new log file from the authoritative data.
        with open(log_path, 'w', newline='', encoding='utf-8') as f_log:
            writer = csv.DictWriter(f_log, fieldnames=['Fingerprint', 'Filename'])
            writer.writeheader()
            writer.writerows(log_rows)
        print(f"INFO: Successfully created '{log_path.name}' with {len(log_rows)} unique entries.")
        return fingerprint_map

    except Exception as e:
        print(f"WARNING: Could not bootstrap download log from source folder. Will start fresh. Error: {e}")
        return {}


def _load_or_create_download_log(log_path: Path, source_folder: Path) -> Dict[str, Path]:
    """
    Loads the download log, or creates it by calling the bootstrap helper if it doesn't exist.

    This provides a persistent cache of downloaded files.

    Args:
        log_path: The path to the simple (Fingerprint, Filename) log file.
        source_folder: The directory where TCX files are stored (for bootstrapping).

    Returns:
        A dictionary mapping each known fingerprint to its corresponding file Path.
    """
    if not log_path.exists():
        # If the log doesn't exist, call the dedicated bootstrap function.
        return _bootstrap_log_from_folder(source_folder, log_path)

    # If the log exists, read it into memory.
    fingerprint_map: Dict[str, Path] = {}
    try:
        with open(log_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('Fingerprint') and row.get('Filename'):
                    fingerprint_map[row['Fingerprint']] = Path(row['Filename'])
    except Exception as e:
        print(f"WARNING: Could not read existing log file '{log_path}'. Error: {e}")
        # Consider re-bootstrapping if the log is corrupt.
        print("Attempting to re-bootstrap the log from the source folder...")
        return _bootstrap_log_from_folder(source_folder, log_path)

    return fingerprint_map


def _append_to_download_log(log_path: Path, fingerprint: str, new_filepath: Path):
    """Appends a new entry to the download log CSV file."""
    file_exists = log_path.exists()
    try:
        with open(log_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['Fingerprint', 'Filename'])
            if not file_exists:
                writer.writeheader()
            writer.writerow({'Fingerprint': fingerprint, 'Filename': str(new_filepath)})
    except IOError as e:
        print(f"ERROR: Could not write to download log '{log_path}'. Error: {e}")


def _load_master_csv(csv_path: Path) -> Dict[str, Dict]:
    """
    Loads the master workout CSV into a dictionary keyed by workout ID for fast lookups.

    Args:
        csv_path: The path to the master workout CSV file (e.g., pk_workouts_test.csv).

    Returns:
        A dictionary mapping each workout_id to its corresponding data row dictionary.
    """
    workout_db: Dict[str, Dict] = {}
    if not csv_path.exists():
        print(f"INFO: Master CSV '{csv_path.name}' not found. Will be created from scratch.")
        return workout_db
    try:
        with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                workout_id = _get_workout_id_from_link(row.get('Link', ''))
                if workout_id:
                    workout_db[workout_id] = row
    except Exception as e:
        print(f"WARNING: Could not load master CSV '{csv_path}'. Will treat all workouts as new. Error: {e}")
        return {}
    return workout_db


def _merge_and_update_workout_lists(
        latest_from_mmr: List[Dict],
        master_db: Dict[str, Dict]
) -> List[Dict]:
    """
    Merges the latest workout list from MapMyRide with the local master database.

    - Uses the latest data from MapMyRide as the source of truth for web metadata.
    - Preserves local-only fields (Filename, Fingerprint, etc.) from the master DB.
    - If a workout was deleted online, it will be correctly excluded from the final list.
    """
    print("\n--- Merging online data with local database ---")
    final_workout_list: List[Dict] = []

    # Get the set of all workout IDs that exist in the latest download.
    latest_ids = {_get_workout_id_from_link(w.get('Link', '')) for w in latest_from_mmr}

    for new_workout_data in latest_from_mmr:
        workout_id = _get_workout_id_from_link(new_workout_data.get('Link', ''))
        if not workout_id:
            continue

        # Start with the existing record from our DB, or an empty dict if it's new.
        final_record = master_db.get(workout_id, {}).copy()

        # Overwrite with the fresh data from the online CSV. This updates fields
        # like Notes, Calories, etc., while preserving local fields not in the
        # online export (like Filename, Fingerprint, TCX Activity ID).
        final_record.update(new_workout_data)
        final_workout_list.append(final_record)

    # Find workouts that were deleted online (in DB but not in the latest pull)
    deleted_ids = set(master_db.keys()) - latest_ids
    if deleted_ids:
        print(
            f"INFO: Detected {len(deleted_ids)} workouts deleted from MapMyRide. They will be removed from the master CSV.")
        for deleted_id in deleted_ids:
            print(f"  - Removed workout ID: {deleted_id}")

    print(f"Merge complete. Processing {len(final_workout_list)} active workouts.")
    return final_workout_list


def login_only(config: configparser.ConfigParser):
    """
    Starts a browser and performs the login action only.

    This is useful for testing credentials or pre-authenticating a browser
    session before running a long synchronization task. The browser will
    remain open after a successful login.

    Args:
        config: The application configuration object.
    """
    print("\n--- LOGIN ONLY MODE ---")
    chrome_options = ChromeOptions()
    # This option keeps the browser window open after the script finishes
    chrome_options.add_experimental_option("detach", True)

    with webdriver.Chrome(options=chrome_options) as browser:
        if _login_to_mapmyride(browser, config):
            print("\nLogin successful. Browser will remain open.")
            print("You can now manually inspect the website or close the browser.")
        else:
            print("\nLogin failed. Please check your credentials and network.")


def _login_to_mapmyride(browser: webdriver.Chrome, config: configparser.ConfigParser) -> bool:
    """
    Handles the login process for MapMyRide.

    Navigates to the login page, waits for elements to be ready, enters credentials,
    and submits the form. This version has an extended timeout to allow for
    manual CAPTCHA completion.

    Args:
        browser: The active Selenium Chrome WebDriver instance.
        config: The application configuration object.

    Returns:
        True if login was successful, False otherwise.

    Raises:
        TimeoutException: If the login page or its elements fail to load in time.
    """
    # Get values from the config object
    login_url = config.get('urls', 'login_url')
    username = config.get('credentials', 'username')
    password = config.get('credentials', 'password')
    password_input_id = config.get('selectors', 'password_input_id')
    email_input_id = config.get('selectors', 'email_input_id')

    print("Navigating to login page...")
    browser.get(login_url)
    try:
        # Use a long WebDriverWait to allow time for manual CAPTCHA solving.
        print("Waiting up to 120 seconds for login page to be ready (for CAPTCHA)...")
        WebDriverWait(browser, 120).until(EC.presence_of_element_located((By.ID, password_input_id)))
        print("Page is ready! Entering credentials...")

        email_el = browser.find_element(by=By.ID, value=email_input_id)
        email_el.send_keys(username)

        pwd_el = browser.find_element(by=By.ID, value=password_input_id)
        pwd_el.send_keys(password)

        login_button_xpath = "//button[contains(., 'Log In')]"
        login_button = browser.find_element(by=By.XPATH, value=login_button_xpath)
        login_button.click()

        # Wait for the URL to change, confirming login has processed and redirected.
        WebDriverWait(browser, 120).until(EC.url_changes(login_url))
        print("Login successful.")
        return True

    except TimeoutException:
        print("Error: Loading login page or finding elements took too much time!")
        return False


def _extract_workout_id_from_filename(path: Path) -> Optional[str]:
    """Extracts the workout ID from a filename like '... (W12345).tcx'."""
    # This pattern looks for '(W' followed by digits, and captures the digits.
    match = re.search(r'\(W(\d+)\)', path.name)
    if match:
        return match.group(1)
    return None


def _scan_folder_and_build_id_map(source_folder: Path) -> Dict[str, Path]:
    """
    Scans the source folder, builds a map of workout IDs to file paths,
    and cleans up any duplicate files for the same workout ID.

    This is now the primary mechanism for detecting duplicates.

    Args:
        source_folder: The directory where TCX files are stored.

    Returns:
        A dictionary mapping each unique workout ID to its single, authoritative file Path.
    """
    print(f"\n--- Scanning for duplicates by Workout ID in '{source_folder.name}' ---")
    if not source_folder.is_dir():
        print(f"ERROR: Source folder not found at '{source_folder}'.")
        return {}

    # Step 1: Group all files by their embedded workout ID
    id_to_files: Dict[str, List[Path]] = {}
    for tcx_path in source_folder.glob("*.tcx"):
        workout_id = _extract_workout_id_from_filename(tcx_path)
        if workout_id:
            if workout_id not in id_to_files:
                id_to_files[workout_id] = []
            id_to_files[workout_id].append(tcx_path)

    # Step 2: Process groups to find and remove duplicates
    authoritative_map: Dict[str, Path] = {}
    files_deleted = 0
    for workout_id, file_list in id_to_files.items():
        if len(file_list) > 1:
            # Sort by name to make the choice of which to keep deterministic
            file_list.sort()
            file_to_keep = file_list[0]
            print(f"  -> Found duplicates for Workout ID {workout_id}. Keeping '{file_to_keep.name}'.")

            # Delete the other files
            for file_to_delete in file_list[1:]:
                print(f"     - Deleting duplicate file: '{file_to_delete.name}'")
                try:
                    os.remove(file_to_delete)
                    files_deleted += 1
                except OSError as e:
                    print(f"     - ERROR: Could not delete file: {e}")

            authoritative_map[workout_id] = file_to_keep
        else:
            # This one is unique
            authoritative_map[workout_id] = file_list[0]

    if files_deleted > 0:
        print(f"Cleanup complete. Deleted {files_deleted} duplicate files.")
    else:
        print("No duplicates found based on workout ID.")

    return authoritative_map


def _process_workouts(
        config: configparser.ConfigParser,
        browser: Optional[webdriver.Chrome],
        latest_workouts: List[Dict],
        existing_workouts_by_fingerprint: Dict[str, Path],
        temp_download_dir: Path,
        login_state: Dict[str, bool]
):
    """
    The core processing loop that iterates through workouts, downloads missing
    files, and prepares the data for the final master CSV.
    This version now also backfills the 'TCX Activity ID' for existing files.
    """
    # Get paths from config
    master_csv_path = Path(config.get('paths', 'tcx_file_list'))
    download_log_path = Path(config.get('paths', 'download_log_file'))
    source_gps_track_folder = Path(config.get('paths', 'source_gps_track_folder'))
    tcx_export_url_template = config.get('urls', 'tcx_export_url_template')

    if not latest_workouts:
        print("No workout data to process.")
        return

    # Dynamically determine all fieldnames from the merged data
    all_keys = set()
    for workout in latest_workouts:
        all_keys.update(workout.keys())

    # Ensure our critical columns are always included and ordered first
    fieldnames = ['Date Submitted', 'Workout Date', 'Activity Type', 'Link', 'Filename', 'Fingerprint', 'TCX Activity ID']
    # Add all other keys, sorted, after the main ones
    other_keys = sorted([key for key in all_keys if key not in fieldnames])
    fieldnames.extend(other_keys)


    # Process all workouts against our log
    all_workouts_to_write = []
    for workout in latest_workouts:
        # Create a fresh copy to modify
        updated_workout = workout.copy()
        workout_id = _get_workout_id_from_link(updated_workout.get('Link', ''))
        if not workout_id:
            all_workouts_to_write.append(updated_workout)
            continue

        # Create a "candidate" fingerprint from the online data.
        try:
            candidate_fingerprint = create_tcx_fingerprint_from_data(
                total_time_seconds=updated_workout.get('Workout Time (seconds)', '0'),
                distance_km=updated_workout.get('Distance (km)', '0')
            )
            updated_workout['Fingerprint'] = candidate_fingerprint
        except (ValueError, TypeError):
            all_workouts_to_write.append(updated_workout)
            continue

        # Check if this candidate or its file already exists.
        should_download = False
        existing_path = existing_workouts_by_fingerprint.get(candidate_fingerprint)

        if not existing_path:
            should_download = True
            print(f"Candidate fingerprint '{candidate_fingerprint}' not in log. Downloading workout ID: {workout_id}")
        elif not existing_path.exists():
            should_download = True
            print(
                f"WARNING: File for known fingerprint '{candidate_fingerprint}' is missing. Re-downloading workout ID: {workout_id}")
        else:
            # File exists and is in the log, no download needed.
            updated_workout['Filename'] = str(existing_path)
            # --- BACKFILL MISSING DATA FOR EXISTING FILES ---
            if not updated_workout.get('TCX Activity ID'):
                print(f"  -> Backfilling TCX metadata for existing file: {existing_path.name}")
                tcx_props = extract_tcx_file_properties(str(existing_path))
                updated_workout['TCX Activity ID'] = tcx_props.get('Id', '')
            # --- END ---

        if should_download:
            if not browser:
                print("ERROR: Browser is not available for download. Please run in online mode.")
                all_workouts_to_write.append(updated_workout)
                continue

            # --- LAZY LOGIN EXECUTION ---
            if not login_state['logged_in']:
                print("\nLogin required for download. Initiating login...")
                if not _login_to_mapmyride(browser, config):
                    print("ERROR: Login failed. Cannot download any more files this run.")
                    break # Exit the loop if login fails
                login_state['logged_in'] = True
            # --- END LAZY LOGIN ---

            downloaded_tcx_path = _download_file_and_wait(browser,
                                                          tcx_export_url_template.format(workout_id=workout_id),
                                                          temp_download_dir)

            if downloaded_tcx_path:
                authoritative_fingerprint = get_fingerprint_from_tcx_file(downloaded_tcx_path)

                if authoritative_fingerprint and authoritative_fingerprint in existing_workouts_by_fingerprint:
                    duplicate_path = existing_workouts_by_fingerprint[authoritative_fingerprint]
                    print(
                        f"      -> INFO: Downloaded file is a duplicate of an existing file: '{duplicate_path.name}' "
                        f"(Authoritative FP: {authoritative_fingerprint}). Discarding.")
                    updated_workout['Fingerprint'] = authoritative_fingerprint
                    updated_workout['Filename'] = str(duplicate_path)
                    try:
                        os.remove(downloaded_tcx_path)
                    except OSError:
                        pass
                elif authoritative_fingerprint:
                    # --- NEW FILENAME CONSTRUCTION LOGIC ---
                    workout_date_str = updated_workout.get('Workout Date', '')
                    date_prefix = _format_date_for_filename(workout_date_str)
                    original_stem = downloaded_tcx_path.stem
                    cleaned_stem = re.sub(r' \(\d+\)$', '', original_stem).strip()
                    new_stem = f"{date_prefix} {cleaned_stem} (W{workout_id})"
                    new_filename = new_stem + downloaded_tcx_path.suffix
                    unique_dest_path = _get_unique_filepath(source_gps_track_folder, new_filename)
                    downloaded_tcx_path.rename(unique_dest_path)
                    # --- END NEW LOGIC ---

                    print(f"      -> Successfully downloaded. Authoritative FP: {authoritative_fingerprint}")
                    print(f"      -> Renamed and moved to: {unique_dest_path.name}")

                    # --- EXTRACT TCX ACTIVITY ID AND ADD TO RECORD ---
                    tcx_props = extract_tcx_file_properties(str(unique_dest_path))
                    updated_workout['TCX Activity ID'] = tcx_props.get('Id', '')
                    # --- END ---

                    _append_to_download_log(download_log_path, authoritative_fingerprint, unique_dest_path)
                    existing_workouts_by_fingerprint[authoritative_fingerprint] = unique_dest_path
                    updated_workout['Fingerprint'] = authoritative_fingerprint
                    updated_workout['Filename'] = str(unique_dest_path)
                else:
                    print(
                        f"      -> ERROR: Failed to generate fingerprint for downloaded workout {workout_id}. Discarding.")
                    updated_workout['Filename'], updated_workout['Fingerprint'] = '', ''
            else:
                print(f"      -> ERROR: Failed to download TCX for workout {workout_id}")
                updated_workout['Filename'], updated_workout['Fingerprint'] = '', ''

        all_workouts_to_write.append(updated_workout)

    # --- SAFEGUARD ---
    if not all_workouts_to_write:
        print("\nWARNING: No workout data was processed successfully. The master CSV file will not be updated.")
        return

    # --- NEW: AUTOMATIC BACKUP CREATION ---
    if master_csv_path.exists():
        backup_path = master_csv_path.with_name(
            f"{master_csv_path.stem}_backup_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        print(f"\nCreating a backup of the current master file to: {backup_path.name}")
        shutil.copy(master_csv_path, backup_path)
    # --- END NEW ---

    # Rewrite the master CSV file with the complete, updated data.
    print(f"\nRewriting master data to '{master_csv_path}' with {len(all_workouts_to_write)} records...")
    with open(master_csv_path, "w", newline="", encoding='utf-8') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_workouts_to_write)
    print("Update complete.")


def export_tcx_files(config: configparser.ConfigParser):
    """
    Orchestrates the workout synchronization process.

    This version is now much more efficient. It loads the existing master CSV as a
    database and intelligently merges it with the latest data downloaded from
    the web, avoiding redundant processing of existing files.

    Args:
        config: The application configuration object.
    """
    # 1. Get configuration
    use_local_csv = config.getboolean('debugging', 'use_local_csv', fallback=False)
    local_csv_path = Path(config.get('debugging', 'local_csv_path'))
    master_csv_path = Path(config.get('paths', 'tcx_file_list'))
    download_log_path = Path(config.get('paths', 'download_log_file'))
    source_gps_track_folder = Path(config.get('paths', 'source_gps_track_folder'))
    csv_export_url = config.get('urls', 'csv_export_url')

    # 2. Load our persistent state: the master CSV and the download log
    print("Loading master workout database...")
    known_workouts_db = _load_master_csv(master_csv_path)
    print(f"Found {len(known_workouts_db)} workouts in the local database.")

    print("\nLoading authoritative download log...")
    existing_workouts_by_fingerprint = _load_or_create_download_log(
        log_path=download_log_path,
        source_folder=source_gps_track_folder
    )
    print(f"Found {len(existing_workouts_by_fingerprint)} existing workouts in the log.")

    # 3. Get the list of latest workouts (either locally or online)
    latest_workouts_from_mmr = []
    temp_download_dir = get_downloads_folder() / f"sel_map_extract_temp_{int(time.time())}"

    # --- SHARED LOGIN STATE ---
    login_state = {'logged_in': False}

    try:
        if use_local_csv and local_csv_path.exists():
            print(f"\n--- DEBUG MODE: Using local CSV file: {local_csv_path} ---")
            with open(local_csv_path, 'r', newline='', encoding='utf-8') as f:
                latest_workouts_from_mmr = list(csv.DictReader(f))
        else:
            # --- Normal Online Mode ---
            temp_download_dir.mkdir(parents=True, exist_ok=True)
            print(f"Using temporary download directory: {temp_download_dir}")

            chrome_options = ChromeOptions()
            prefs = {"download.default_directory": str(temp_download_dir)}
            chrome_options.add_experimental_option("prefs", prefs)

            with webdriver.Chrome(options=chrome_options) as browser:
                print("\nLogin required to download master workout list...")
                if not _login_to_mapmyride(browser, config):
                    print("FATAL: Login failed. Cannot retrieve workout list. Aborting.")
                    return
                login_state['logged_in'] = True

                print("\nDownloading latest workout CSV from MapMyRide...")
                latest_workouts_csv_path = _download_file_and_wait(browser, csv_export_url, temp_download_dir)
                if not latest_workouts_csv_path:
                    print("Failed to download the main workout CSV. Aborting.")
                    return

                # Always save a copy for debugging
                shutil.copy(latest_workouts_csv_path, local_csv_path)
                print(f"INFO: A debug copy of the workout list has been saved to:\n      {local_csv_path}")

                with open(latest_workouts_csv_path, 'r', newline='', encoding='utf-8') as f:
                    latest_workouts_from_mmr = list(csv.DictReader(f))

                # --- This block is now part of the main online flow and no longer needs a separate 'else' ---
                validated_workouts, errors_found = _validate_and_deduplicate_workouts(latest_workouts_from_mmr)
                if errors_found:
                    print("WARNING: Data integrity issues were found. The sync will continue with only the valid workouts.")

                merged_workouts = _merge_and_update_workout_lists(validated_workouts, known_workouts_db)

                _process_workouts(config, browser, merged_workouts, existing_workouts_by_fingerprint, temp_download_dir,
                                  login_state)
                # The browser context will automatically close here
            return # Exit after successful online run

        # --- This block now handles the DEBUG/local CSV case explicitly ---
        if latest_workouts_from_mmr:
            validated_workouts, errors_found = _validate_and_deduplicate_workouts(latest_workouts_from_mmr)
            if errors_found:
                print("WARNING: Data integrity issues were found. The sync will continue with only the valid workouts.")

            merged_workouts = _merge_and_update_workout_lists(validated_workouts, known_workouts_db)

            # Start browser for downloads if needed, but don't log in yet
            chrome_options = ChromeOptions()
            prefs = {"download.default_directory": str(temp_download_dir)}
            chrome_options.add_experimental_option("prefs", prefs)
            temp_download_dir.mkdir(parents=True, exist_ok=True)
            with webdriver.Chrome(options=chrome_options) as browser:
                _process_workouts(config, browser, merged_workouts, existing_workouts_by_fingerprint, temp_download_dir,
                                  login_state)

    finally:
        if temp_download_dir.exists():
            try:
                shutil.rmtree(temp_download_dir)
                print(f"Cleaned up temporary directory: {temp_download_dir}")
            except OSError as e:
                print(f"Warning: Could not remove temporary directory {temp_download_dir}: {e}")




def get_last_downloaded_file(path_to_downloads: str = '', wait_for_tmp_files: bool = False, max_retries: int = 3):
    """
    Gets the file with the most recent timestamp in the specified folder

    :param wait_for_tmp_files: true if the function should wait if it gets a file with the .tmp ext and retry to see
    if it's a file that's in the process of being downloaded hence may change name
    :param path_to_downloads: str with path to Downloads folder (if blank then gets downloads folder from env)
    :param max_retries: maximum number of retries if sorting fails
    :return: path to the last downloaded file or None if no files found
    """

    if not path_to_downloads:
        path_to_downloads = get_downloads_folder()

    # Time to sleep if needed to wait for a temp file to download
    sleep_time = 1
    for _ in range(max_retries):
        try:
            files = os.listdir(path_to_downloads)
            if files:
                # Sort files by modification time (newest first)
                files.sort(key=lambda x: os.path.getmtime(os.path.join(path_to_downloads, x)), reverse=True)
                last_downloaded_file = os.path.join(path_to_downloads, files[0])
                if wait_for_tmp_files:
                    file_basename, file_extension = os.path.splitext(last_downloaded_file)
                    if file_extension == '.tmp' or file_extension == '.crdownload':
                        time.sleep(sleep_time)
                        sleep_time += sleep_time
                        continue
                    else:
                        return last_downloaded_file
                else:
                    return last_downloaded_file
            else:
                return None  # No files found in the directory
        except Exception as e:
            print(f"Error sorting files: {e}")
            # Retry the function

    return None  # Exceeded max retries without success


def get_downloads_folder() -> Path:
    """
    Returns the path to the user's Downloads folder as a Path object.

    Returns:
        Path: A pathlib.Path object representing the Downloads folder.
    """
    return Path.home() / "Downloads"


def create_tcx_route_map(config: configparser.ConfigParser):
    """Creates a Folium map of all walk/hike workouts from the master CSV."""
    tcx_file_list = config.get('paths', 'tcx_file_list')

    # Create map centered on a reasonable location
    route_map = folium.Map(
        location=[-31.6, 115.7],
        zoom_start=10,
        tiles='OpenStreetMap'
    )

    try:
        with open(tcx_file_list, 'r', encoding='UTF8') as csvfile:
            reader = csv.DictReader(csvfile)
            for workout in reader:
                activity_type = workout.get('Activity Type', '')
                if activity_type in ('Walk', 'Hike'):
                    filename = workout.get('Filename')
                    if not filename or not os.path.exists(filename):
                        print(f"Skipping workout, file not found: {filename}")
                        continue

                    p = Path(filename)
                    route_desc = f"{workout.get('Workout Date', '')} {p.stem}"
                    print(f"Processing: {route_desc}")

                    # Using a robust parser to get all points
                    coordinates = parse_all_points_from_tcx(filename)

                    # The coordinates from parse_all_points_from_tcx are (lon, lat)
                    # Folium's PolyLine expects (lat, lon), so we need to swap them
                    folium_coords = [(lat, lon) for lon, lat in coordinates]

                    if folium_coords:
                        folium.PolyLine(folium_coords, weight=4, color='red').add_to(route_map)
                        folium.Marker(
                            location=folium_coords[0],
                            popup=route_desc
                        ).add_to(route_map)

        map_output_path = 'index.html'
        route_map.save(map_output_path)
        print(f"\nMap saved to {map_output_path}")

    except FileNotFoundError:
        print(f"Error: Master CSV file not found at '{tcx_file_list}'")
    except Exception as e:
        print(f"An unexpected error occurred while creating the map: {e}")


def create_tcx_route_map_copilot():
    # This function appears to be experimental and uses hardcoded example data.
    # It can be left as-is or refactored similarly to create_tcx_route_map if it becomes a primary function.
    route_map = folium.Map(
        location=[-31.6, 115.7],
        zoom_start=10,
        tiles='OpenStreetMap'
    )
    net1 = folium.FeatureGroup(name='net1', overlay=True, control=True)
    net2 = folium.FeatureGroup(name='net2', overlay=True, control=True)
    with open(r"C:\Users\krant\Downloads\pk_workouts_test.csv", 'r', encoding='UTF8') as csvfile:
        reader = csv.DictReader(csvfile)
        for workout in reader:
            activity_type = workout['Activity Type']
            if activity_type == 'Walk' or activity_type == 'Hike':
                filename = workout['Filename']
                p = Path(filename)
                route_desc = f"{workout['Workout Date']} {p.stem}"
                print(route_desc)
                coordinates = [(lat, lon) for lat, lon in [(31.5, 115.8), (31.6, 115.9)]]
                if coordinates:
                    polyline = folium.PolyLine(coordinates, weight=4, color='red')
                    polyline.add_to(route_map)
                    if activity_type == 'Walk':
                        net1.add_child(polyline)
                    elif activity_type == 'Hike':
                        net2.add_child(polyline)
                    for coord in coordinates:
                        folium.Marker(
                            location=coord,
                            popup=route_desc,
                            icon=folium.Icon(icon='cloud'),
                        ).add_to(route_map)
    route_map.add_child(net1)
    route_map.add_child(net2)
    folium.LayerControl().add_to(route_map)
    route_map.save('index_copilot.html')


def highlight_track(e):
    e.target.setStyle({'color': 'blue', 'weight': 6, 'opacity': 1})


def reset_track(e):
    e.target.setStyle({'color': 'red', 'weight': 4, 'opacity': 1})


def parse_all_points_from_tcx(tcx_file: str) -> list[tuple[float, float]]:
    """
    Parses a TCX file and extracts all track points into a list of coordinates.

    Args:
        tcx_file: The path to the TCX file.

    Returns:
        A list of (longitude, latitude) tuples for every trackpoint in the file.
        Returns an empty list if the file cannot be parsed or has no points.
    """
    try:
        tree = ET.parse(tcx_file)
        root = tree.getroot()
        ns = {'ns': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    except ET.ParseError:
        print(f"Error: Could not parse XML in file: {tcx_file}")
        return []

    coordinates = []
    trackpoints = root.findall('.//ns:Trackpoint', ns)

    for point in trackpoints:
        try:
            lat_el = point.find('.//ns:LatitudeDegrees', ns)
            lon_el = point.find('.//ns:LongitudeDegrees', ns)
            if lat_el is not None and lon_el is not None:
                lat = float(lat_el.text)
                lon = float(lon_el.text)
                coordinates.append((lon, lat))
        except (ValueError, AttributeError):
            # Skip malformed points
            continue

    return coordinates


def create_simplified_geojson_from_tcx_geopandas(tcx_file: str, geojson_file: str, tolerance: float = 10.0):
    """
    Reads a TCX file, simplifies its track using GeoPandas, and writes it to a GeoJSON file.
    """
    print(f"Reading all points from '{os.path.basename(tcx_file)}'...")
    all_points = parse_all_points_from_tcx(tcx_file)

    if not all_points or len(all_points) < 2:
        print("Not enough points found to create a line. Aborting.")
        return

    print(f"Original point count: {len(all_points)}")

    line = LineString(all_points)
    gdf = gpd.GeoDataFrame([1], geometry=[line], crs="EPSG:4326")
    gdf = gdf.to_crs("EPSG:3857")  # Project to meters for accurate tolerance

    print(f"Simplifying track with tolerance {tolerance} meters...")
    gdf['geometry'] = gdf.simplify(tolerance, preserve_topology=True)

    gdf = gdf.to_crs("EPSG:4326")  # Project back to WGS84 for GeoJSON

    simplified_line = gdf.iloc[0].geometry
    print(f"Simplified point count: {len(simplified_line.coords)}")

    feature = geojson.Feature(geometry=simplified_line, properties={"source_file": os.path.basename(tcx_file)})
    feature_collection = geojson.FeatureCollection([feature])

    try:
        with open(geojson_file, 'w') as f:
            geojson.dump(feature_collection, f, indent=2)
        print(f"Successfully created simplified GeoJSON: '{os.path.basename(geojson_file)}'")
    except IOError as e:
        print(f"Error writing to file: {e}")


def simplify_all_tcx_files(config: configparser.ConfigParser, only_if_missing: bool = True):
    """
    Reads the master CSV and creates simplified GeoJSON files for all workouts.

    Args:
        config: The application configuration object.
        only_if_missing: If True (default), only creates a GeoJSON file if one
                         does not already exist. If False, it will overwrite
                         any existing GeoJSON files.
    """
    if only_if_missing:
        print("\n--- Simplifying TCX files (Incremental Mode) ---")
        print("Only creating GeoJSON for workouts that are missing a file.")
    else:
        print("\n--- Simplifying TCX files (Full Rebuild Mode) ---")
        print("This will overwrite any existing GeoJSON files.")

    tcx_file_list = config.get('paths', 'tcx_file_list')
    source_folder = Path(config.get('paths', 'source_gps_track_folder'))
    simplified_folder = Path(config.get('paths', 'simplified_gps_track_folder'))

    simplified_folder.mkdir(parents=True, exist_ok=True)

    try:
        workouts: list[dict[str, str]] = read_known_tcx_file_csv(tcx_file_list=tcx_file_list)

        created_count = 0
        skipped_count = 0
        error_count = 0

        walk_hike_workouts = [w for w in workouts if w.get('Activity Type', '') in ('Walk', 'Hike')]
        print(f"Found {len(walk_hike_workouts)} Walk/Hike workouts to process.")

        for workout in walk_hike_workouts:
            original_filepath = workout.get('Filename')
            if not original_filepath:
                continue

            filename = Path(original_filepath).name
            src_path = source_folder / filename
            dest_path = simplified_folder / (src_path.stem + '.geojson')

            # The main logic change is here:
            if not only_if_missing or not dest_path.exists():
                if src_path.exists():
                    create_simplified_geojson_from_tcx_geopandas(
                        tcx_file=str(src_path),
                        geojson_file=str(dest_path),
                        tolerance=10.0
                    )
                    created_count += 1
                else:
                    print(f"  - WARNING: Source file not found, cannot simplify: {src_path}")
                    error_count += 1
            else:
                skipped_count += 1

        print("\n--- Simplification Summary ---")
        print(f"GeoJSON files created/updated: {created_count}")
        print(f"Files skipped (already exist): {skipped_count}")
        if error_count > 0:
            print(f"Errors (source file not found): {error_count}")

    except FileNotFoundError:
        print(f"ERROR: Master CSV file not found at '{tcx_file_list}'")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


def migrate_filenames_to_workout_id_schema(config: configparser.ConfigParser, dry_run: bool = True):
    """
    Performs a one-time, resilient migration to rename TCX files and update the master CSV.

    This version is enhanced to be robust against duplicate filenames in the source CSV.
    It will process all valid files and gracefully skip duplicate entries, flagging them
    in the final output CSV for review.

    Args:
        config: The application configuration object.
        dry_run: If True (default), only lists the actions that would be taken.
                 If False, performs the file renames and creates the new CSV.
    """
    if dry_run:
        print("\n--- Migrating filenames (DRY RUN MODE) ---")
        print("No files will be changed. Set dry_run=False to execute.")
    else:
        print("\n--- Migrating filenames (EXECUTION MODE) ---")

    master_csv_path = Path(config.get('paths', 'tcx_file_list'))

    if not master_csv_path.exists():
        print(f"ERROR: Master CSV file not found at '{master_csv_path}'. Cannot proceed.")
        return

    try:
        with open(master_csv_path, 'r', newline='', encoding='utf-8') as f:
            workouts = list(csv.DictReader(f))
            if not workouts:
                print("ERROR: The master CSV file is empty.")
                return
            # Ensure the 'Notes' field exists in the header for writing later
            fieldnames = list(workouts[0].keys())
            if 'Notes' not in fieldnames:
                print("Warning: 'Notes' column not found in CSV. It will be added.")
                fieldnames.append('Notes')
    except Exception as e:
        print(f"ERROR: Could not read master CSV file. Reason: {e}")
        return

    # --- PHASE 1: PLAN THE MIGRATION ---
    print("\n--- Phase 1: Planning ---")
    print("Verifying all files and checking for data integrity issues before making any changes...")

    renames_to_perform: List[Tuple[Path, Path]] = []
    updated_workout_rows: List[Dict] = []
    duplicate_count = 0

    # Keep track of source files and the workout ID that first claimed them.
    source_files_in_plan: Dict[Path, str] = {}

    for i, workout_data in enumerate(workouts):
        original_filepath_str = workout_data.get('Filename', '')
        link = workout_data.get('Link', '')

        if not all([original_filepath_str, link]):
            print(f"Row {i + 1}: Skipping due to missing Filename or Link.")
            updated_workout_rows.append(workout_data)
            continue

        current_filepath = Path(original_filepath_str)
        workout_id = _get_workout_id_from_link(link)

        if not workout_id:
            print(f"Row {i + 1}: Skipping '{current_filepath.name}' (could not get workout ID).")
            updated_workout_rows.append(workout_data)
            continue

        if not current_filepath.exists():
            print(f"Row {i + 1}: File not found '{current_filepath.name}'. Skipping.")
            updated_workout_rows.append(workout_data)
            continue

        # --- ROBUST DUPLICATE HANDLING ---
        if current_filepath in source_files_in_plan:
            original_workout_id = source_files_in_plan[current_filepath]
            print(
                f"Row {i + 1}: ❌ DATA WARNING: The file '{current_filepath.name}' was already claimed by workout "
                f"{original_workout_id}. This row (for workout {workout_id}) is a duplicate and will be skipped.")
            duplicate_count += 1

            # Modify the row for the new CSV to flag it as a duplicate
            updated_row = workout_data.copy()
            updated_row['Filename'] = ''  # Clear the filename to indicate it's not linked
            notes = updated_row.get('Notes', '')
            updated_row[
                'Notes'] = f"DUPLICATE DATA: This workout points to the same file as workout ID {original_workout_id}. {notes}".strip()
            updated_workout_rows.append(updated_row)
            continue
        # --- END DUPLICATE HANDLING ---

        try:
            tcx_props = extract_tcx_file_properties(str(current_filepath))
            csv_time_sec = workout_data.get('Workout Time (seconds)', '0')
            tcx_time_sec = tcx_props.get('TotalTimeSeconds', '0')
            duration_match = int(round(float(csv_time_sec))) == int(round(float(tcx_time_sec)))

            if not duration_match:
                print(f"Row {i + 1}: Verification FAILED for '{current_filepath.name}'. Skipping.")
                updated_workout_rows.append(workout_data)
                continue
        except Exception as e:
            print(f"Row {i + 1}: ERROR verifying '{current_filepath.name}': {e}. Skipping.")
            updated_workout_rows.append(workout_data)
            continue

        # --- NEW FILENAME CONSTRUCTION LOGIC ---
        # 1. Get the date prefix
        workout_date_str = workout_data.get('Workout Date', '')
        date_prefix = _format_date_for_filename(workout_date_str)

        # 2. Clean the original filename stem to remove spurious numbers
        original_stem = current_filepath.stem
        cleaned_stem = re.sub(r' \(\d+\)$', '', original_stem).strip()

        # 3. Combine parts into the new filename
        new_stem = f"{date_prefix} {cleaned_stem} (W{workout_id})"
        new_filepath = current_filepath.with_name(f"{new_stem}{current_filepath.suffix}")

        renames_to_perform.append((current_filepath, new_filepath))
        source_files_in_plan[current_filepath] = workout_id

        updated_row = workout_data.copy()
        updated_row['Filename'] = str(new_filepath)
        updated_workout_rows.append(updated_row)

    print(f"\nPlanning Complete. Found {len(renames_to_perform)} files to rename.")
    print(f"Found and skipped {duplicate_count} duplicate data entries.")

    if not renames_to_perform:
        print("\n✅ No files need to be renamed. Your library is already up to date.")
        return

    # --- PHASE 2: EXECUTE THE RENAMES ---
    renamed_count = 0
    if dry_run:
        print("\n--- Phase 2: Planning Renames (Dry Run) ---")
        for old_path, new_path in renames_to_perform:
            print(f"  - Would rename '{old_path.name}' -> '{new_path.name}'")
        renamed_count = len(renames_to_perform)
    else:
        print("\n--- Phase 2: Executing Renames ---")
        for old_path, new_path in renames_to_perform:
            try:
                print(f"  - Renaming '{old_path.name}' -> '{new_path.name}'")
                old_path.rename(new_path)
                renamed_count += 1
            except OSError as e:
                print(f"  - ❌ RENAME FAILED for '{old_path.name}'. Reason: {e}")
                print("\nFATAL: A rename operation failed. Stopping execution to preserve state.")
                return

    # --- PHASE 3: COMMIT THE CHANGES TO A NEW CSV ---
    if dry_run:
        print("\n--- Phase 3: Planning CSV Creation (Dry Run) ---")
        new_csv_path = master_csv_path.with_name(f"{master_csv_path.stem}_migrated.csv")
        print(f"A new master file would be written to '{new_csv_path}' with {len(updated_workout_rows)} rows.")
    else:
        print("\n--- Phase 3: Committing Changes ---")
        new_csv_path = master_csv_path.with_name(f"{master_csv_path.stem}_migrated.csv")
        print(f"Writing updated file list to '{new_csv_path}'...")
        try:
            with open(new_csv_path, 'w', newline='', encoding='utf-8') as f:
                # Use the potentially updated fieldnames list
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(updated_workout_rows)
            print(f"✅ Successfully created new master file with updated filenames.")
            print(f"You should now update your config.ini to point to this new file: '{new_csv_path.name}'")
        except Exception as e:
            print(f"❌ FATAL ERROR: Could not write new master CSV file. Reason: {e}")

    print("\n--- Migration Summary ---")
    if dry_run:
        print(f"Files to be renamed: {renamed_count}")
    else:
        print(f"Files Renamed: {renamed_count}")


def strip_workout_ids_from_filenames(config: configparser.ConfigParser, dry_run: bool = True):
    """
    Scans the source folder and removes any (W...) workout ID suffixes from filenames.
    This is a utility to reset files to their original state before a migration.
    This version removes multiple suffixes in a single pass.

    Args:
        config: The application configuration object.
        dry_run: If True (default), only lists the files that would be renamed
                 without actually changing them. If False, performs the rename.
    """
    if dry_run:
        print("\n--- Stripping Workout IDs from Filenames (DRY RUN MODE) ---")
        print("No files will be changed. Set dry_run=False to execute.")
    else:
        print("\n--- Stripping Workout IDs from Filenames (EXECUTION MODE) ---")

    source_folder = Path(config.get('paths', 'source_gps_track_folder'))

    # Regex to find one or more occurrences of a space followed by '(W...)'
    pattern = re.compile(r"( \(W\d+\))+$")

    reverted_count = 0
    error_count = 0

    if not source_folder.exists():
        print(f"ERROR: Source folder not found at '{source_folder}'.")
        return

    all_tcx_files = list(source_folder.glob("*.tcx"))
    print(f"Scanning {len(all_tcx_files)} files in '{source_folder}'...")

    for filepath in all_tcx_files:
        match = pattern.search(filepath.stem)
        if match:
            try:
                # Create the new stem by removing all matched patterns
                new_stem = filepath.stem.replace(match.group(0), '')
                new_filepath = filepath.with_stem(new_stem)

                if new_filepath.exists() and not dry_run:
                    print(
                        f"  - WARNING: Cannot rename '{filepath.name}' because target '{new_filepath.name}' already exists. Skipping.")
                    error_count += 1
                    continue

                if dry_run:
                    print(f"  - Would revert '{filepath.name}' to '{new_filepath.name}'")
                else:
                    print(f"  - Reverting '{filepath.name}' to '{new_filepath.name}'")
                    filepath.rename(new_filepath)

                reverted_count += 1

            except OSError as e:
                print(f"  - ❌ ERROR: Failed to process '{filepath.name}'. Reason: {e}")
                error_count += 1

    print("\n--- Cleanup Summary ---")
    if dry_run:
        print(f"Files that would be reverted: {reverted_count}")
    else:
        print(f"Files Reverted: {reverted_count}")
    print(f"Errors/Skipped: {error_count}")


def _validate_and_deduplicate_workouts(workouts: List[Dict]) -> Tuple[List[Dict], bool]:
    """
    Validates a list of workouts from the MapMyRide CSV for duplicate workout IDs.

    This version has been refactored to use the centralized, high-precision
    fingerprint function, ensuring a single, consistent method for identifying
    true duplicates.

    Args:
        workouts: The raw list of workout dictionaries from the CSV.

    Returns:
        A tuple containing:
        - A cleaned list of workout dictionaries with duplicates handled.
        - A boolean flag that is True if fatal data conflicts were found.
    """
    print("\n--- Validating Source Data for Duplicates using High-Precision Fingerprints ---")

    fatal_errors_found = False
    clean_workouts: List[Dict] = []
    # {workout_id: first_workout_dict}
    seen_workouts: Dict[str, Dict] = {}
    # A set of workout IDs that have conflicting data
    ids_with_fatal_error: Set[str] = set()

    for workout in workouts:
        workout_id = _get_workout_id_from_link(workout.get('Link', ''))
        if not workout_id:
            continue

        if workout_id in ids_with_fatal_error:
            continue

        if workout_id in seen_workouts:
            first_occurrence = seen_workouts[workout_id]

            # Generate fingerprints for both records to compare them robustly
            fp1 = create_tcx_fingerprint_from_data(
                total_time_seconds=first_occurrence.get('Workout Time (seconds)', '0'),
                distance_km=first_occurrence.get('Distance (km)', '0')
            )
            fp2 = create_tcx_fingerprint_from_data(
                total_time_seconds=workout.get('Workout Time (seconds)', '0'),
                distance_km=workout.get('Distance (km)', '0')
            )

            is_true_duplicate = (fp1 == fp2)

            if is_true_duplicate:
                print(f"  - INFO: Found true duplicate for workout ID {workout_id} (Fingerprint: {fp1}). Keeping first instance.")
                continue  # Skip this duplicate
            else:
                # This is a fatal data integrity error.
                fatal_errors_found = True
                ids_with_fatal_error.add(workout_id)

                print("\n" + "=" * 80)
                print(f"  ❌❌❌ FATAL DATA ERROR: Workout ID {workout_id} has conflicting fingerprints! ❌❌❌")
                print("  This workout will be SKIPPED. Please resolve this manually in your MapMyRide history.")
                print(f"  - First Instance FP:  {fp1}")
                print(f"  - Conflict Found FP:  {fp2}")
                print("=" * 80 + "\n")
                continue

        seen_workouts[workout_id] = workout
        clean_workouts.append(workout)

    if fatal_errors_found:
        print("Filtering out all workouts associated with the fatal data errors listed above...")
        final_workouts = [w for w in clean_workouts if
                          _get_workout_id_from_link(w.get('Link')) not in ids_with_fatal_error]
        print(f"Proceeding with {len(final_workouts)} valid workouts.")
        return final_workouts, True

    print("Validation complete. No data conflicts found.")
    return clean_workouts, False


def create_property_csv_from_folder(config: configparser.ConfigParser):
    """
    Scans all TCX files in the source folder, extracts their properties,
    and writes them to a new CSV file in the configured project path.

    This is a utility function to help inspect the state of the local TCX
    file library before performing a migration or other major operation.

    Args:
        config: The application configuration object.
    """
    print("\n--- Creating TCX Property Report from Folder ---")
    source_folder = Path(config.get('paths', 'source_gps_track_folder'))
    # Get the project folder from the config to keep output files organized
    project_folder = Path(config.get('paths', 'project_path'))

    # Define a destination path for the report inside the project directory
    report_filename = f"tcx_property_report_{datetime.now():%Y%m%d_%H%M%S}.csv"
    report_csv_path = project_folder / report_filename

    # Ensure the project folder exists, creating it if necessary
    project_folder.mkdir(parents=True, exist_ok=True)

    if not source_folder.exists():
        print(f"ERROR: Source folder not found at '{source_folder}'.")
        return

    all_tcx_files = sorted(list(source_folder.glob("*.tcx")))  # Sort for consistent order
    if not all_tcx_files:
        print(f"No TCX files found in '{source_folder}'.")
        return

    print(f"Scanning {len(all_tcx_files)} files in '{source_folder}'...")

    all_file_properties = []
    for tcx_path in all_tcx_files:
        print(f"  - Processing: {tcx_path.name}")
        properties = extract_tcx_file_properties(str(tcx_path))

        # Add the filename and generate a fingerprint for completeness
        properties['Filename'] = tcx_path.name
        # Call the fingerprint function in silent mode
        properties['Fingerprint'] = get_fingerprint_from_tcx_file(tcx_path, silent=True) or "N/A"

        all_file_properties.append(properties)

    # Define the headers for the CSV file.
    # This dynamically finds all possible keys and orders them logically.
    all_keys = set()
    for prop_dict in all_file_properties:
        all_keys.update(prop_dict.keys())

    preferred_order = [
        'Filename', 'Fingerprint', 'Id', 'Activity',
        'TotalTimeSeconds', 'DistanceMeters',
        'StartTime', 'LatitudeDegrees', 'LongitudeDegrees'
    ]
    # Create a sorted list of headers with the preferred ones first
    fieldnames = sorted(list(all_keys),
                        key=lambda x: preferred_order.index(x) if x in preferred_order else len(preferred_order))

    print(f"\nWriting property report to: {report_csv_path}")
    try:
        with open(report_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_file_properties)
        print(f"✅ Report created successfully: {report_csv_path}")
    except IOError as e:
        print(f"❌ ERROR: Could not write report CSV file. Reason: {e}")


def cleanup_numeric_suffixes(config: configparser.ConfigParser, dry_run: bool = True):
    """
    Scans the source folder and removes numeric suffixes like `_0001`.

    This utility handles two cases:
    1. Misplaced suffixes before a workout ID (e.g., `..._0001 (W123).tcx`)
    2. Trailing suffixes at the very end of the filename (e.g., `... (W123)_0001.tcx`)

    The patterns are specifically targeted to `_000[1-9]` to avoid
    accidentally removing numbers that might be years (e.g., `_2023`).

    The regex for the misplaced suffix uses a "positive lookahead" `(?= ...)`
    to find a numeric suffix only if it is immediately followed by the
    workout ID, without making the workout ID part of the match itself.

    Args:
        config: The application configuration object.
        dry_run: If True, only lists renames. If False, performs them.
    """
    if dry_run:
        print("\n--- Cleaning Up Numeric Suffixes (DRY RUN MODE) ---")
        print("No files will be changed. Set dry_run=False to execute.")
    else:
        print("\n--- Cleaning Up Numeric Suffixes (EXECUTION MODE) ---")

    source_folder = Path(config.get('paths', 'source_gps_track_folder'))

    # Regex for misplaced suffixes: `_000[1-9]` before ` (W...)`
    misplaced_pattern = re.compile(r"(_000[1-9])(?= \(W\d+\)$)")
    # Regex for trailing suffixes: `_000[1-9]` at the very end
    trailing_pattern = re.compile(r"_000[1-9]$")

    renamed_count = 0
    error_count = 0

    if not source_folder.exists():
        print(f"ERROR: Source folder not found at '{source_folder}'.")
        return

    all_tcx_files = sorted(list(source_folder.glob("*.tcx")))
    print(f"Scanning {len(all_tcx_files)} files in '{source_folder}'...")

    for filepath in all_tcx_files:
        stem = filepath.stem
        new_stem = None

        # Check for misplaced suffix first
        match = misplaced_pattern.search(stem)
        if match:
            new_stem = misplaced_pattern.sub('', stem)
        else:
            # If no misplaced suffix, check for a trailing one
            match = trailing_pattern.search(stem)
            if match:
                new_stem = trailing_pattern.sub('', stem)

        if new_stem and new_stem != stem:
            try:
                new_filepath = filepath.with_stem(new_stem)

                if new_filepath.exists() and not dry_run:
                    print(
                        f"  - WARNING: Cannot rename '{filepath.name}' because target "
                        f"'{new_filepath.name}' already exists. Skipping.")
                    error_count += 1
                    continue

                if dry_run:
                    print(f"  - Would rename '{filepath.name}' -> '{new_filepath.name}'")
                else:
                    print(f"  - Renaming '{filepath.name}' -> '{new_filepath.name}'")
                    filepath.rename(new_filepath)

                renamed_count += 1
            except OSError as e:
                print(f"  - ❌ ERROR: Failed to process '{filepath.name}'. Reason: {e}")
                error_count += 1

    print("\n--- Cleanup Summary ---")
    if dry_run:
        print(f"Files that would be renamed: {renamed_count}")
    else:
        print(f"Files Renamed: {renamed_count}")
    print(f"Errors/Skipped: {error_count}")


def find_unreferenced_tcx_files(config: configparser.ConfigParser):
    """
    Diagnoses which TCX files in the source folder are not referenced in the master CSV.

    This utility function helps identify "orphaned" files by:
    1.  Getting a list of all TCX files in the source directory.
    2.  Getting a list of all TCX files referenced in the master CSV (`pk_workouts.csv`).
    3.  Finding the files that exist on disk but are not in the CSV.
    4.  For each unreferenced file, it calculates its fingerprint and checks if that
        fingerprint already exists in the master CSV, suggesting it's a likely duplicate.
    5.  If the fingerprint is unique, it flags the file as a potential import anomaly.
    """
    print("\n--- Finding Unreferenced TCX Files ---")

    # 1. Get paths from config
    source_folder = Path(config.get('paths', 'source_gps_track_folder'))
    master_csv_path = Path(config.get('paths', 'tcx_file_list'))

    # 2. Validate paths
    if not source_folder.is_dir():
        print(f"ERROR: Source folder not found at '{source_folder}'.")
        return
    if not master_csv_path.is_file():
        print(f"ERROR: Master CSV file not found at '{master_csv_path}'.")
        return

    # 3. Read all files from the folder
    print(f"Scanning directory: {source_folder}")
    try:
        files_in_folder = {p for p in source_folder.glob("*.tcx")}
        print(f"Found {len(files_in_folder)} TCX files on disk.")
    except Exception as e:
        print(f"ERROR: Could not read files from source folder. Reason: {e}")
        return

    # 4. Read all referenced files and fingerprints from the master CSV
    print(f"Reading master CSV: {master_csv_path.name}")
    files_in_csv = set()
    fingerprints_in_csv: Dict[str, str] = {}
    try:
        with open(master_csv_path, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = row.get('Filename')
                fingerprint = row.get('Fingerprint')
                if filename:
                    files_in_csv.add(Path(filename))
                if fingerprint and filename:
                    # Map the fingerprint to the basename for easier reporting
                    fingerprints_in_csv[fingerprint] = Path(filename).name
        print(f"Found {len(files_in_csv)} file references in the master CSV.")
    except Exception as e:
        print(f"ERROR: Could not read or process master CSV file. Reason: {e}")
        return

    # 5. Find the unreferenced files
    unreferenced_files = sorted(list(files_in_folder - files_in_csv))

    if not unreferenced_files:
        print("\n✅ All TCX files in the source folder are correctly referenced in the master CSV.")
        return

    # 6. Investigate and report on each unreferenced file
    print(f"\nFound {len(unreferenced_files)} unreferenced TCX files. Investigating...")
    print("-" * 30)
    for orphan_path in unreferenced_files:
        print(f"File: {orphan_path.name}")
        orphan_fp = get_fingerprint_from_tcx_file(orphan_path, silent=True)

        if not orphan_fp:
            print("  -> Reason: Could not generate a fingerprint. The file may be corrupt or invalid.")
        elif orphan_fp in fingerprints_in_csv:
            matched_file = fingerprints_in_csv[orphan_fp]
            print(
                f"  -> Reason: LIKELY DUPLICATE. Its fingerprint '{orphan_fp}' matches an existing file in the database: '{matched_file}'")
        else:
            print(
                f"  -> Reason: UNKNOWN. This file has a unique fingerprint '{orphan_fp}' but is not in the master CSV.")
        print("-" * 30)


# ==============================================================================
# MAIN EXECUTION BLOCK
# ==============================================================================
if __name__ == '__main__':
    # 1. Create a config parser and read the config.ini file
    app_config = configparser.ConfigParser()
    app_config.read('config.ini')

    # --- CHOOSE WHICH ACTION TO RUN ---

    # Action: Diagnostic tool to find files on disk that aren't in the master CSV.
    # find_unreferenced_tcx_files(config=app_config)

    # Action: Utility to clean up misplaced or trailing numeric suffixes like `_0001` from filenames.
    # cleanup_numeric_suffixes(config=app_config, dry_run=False)

    # Action: Create a CSV report of all TCX files in your source folder.
    # create_property_csv_from_folder(config=app_config)

    # Action 1: Run the one-time migration to rename files with their workout ID.
    # strip_workout_ids_from_filenames(config=app_config, dry_run=False)
    # migrate_filenames_to_workout_id_schema(config=app_config, dry_run=False)

    # Action 1: Perform a detailed, side-by-side comparison of the first 10 workouts.
    # test_fingerprint_matching_detailed(config=app_config, limit=10)

    # Action 2: Run the full sync process.
    # Set use_local_csv=true in config.ini for a faster dry run.
    export_tcx_files(config=app_config)

    # --- TEST THE NEW FINGERPRINT FUNCTION ---
    # This block allows you to test the fingerprint generation for a single file.
    # problem_file_path = Path(r"C:\Users\krant\GPSTrackData\Fulufallen return walk to falls.tcx")
    # print(f"\n--- DEBUGGING FINGERPRINT FOR ---")
    # print(f"FILE: {problem_file_path}")
    # generated_fingerprint = get_fingerprint_from_tcx_file(problem_file_path)
    # if generated_fingerprint:
    #     print(f"GENERATED FINGERPRINT: {generated_fingerprint}")
    # else:
    #     print("Fingerprint could not be generated.")
    # -----------------------------------------

    # Action 2: Create simplified GeoJSON files from the TCX files listed in the master CSV.
    simplify_all_tcx_files(config=app_config)

    # Action 3: Create an HTML map from the workouts in the master CSV.
    # create_tcx_route_map(config=app_config)

    # --- Other utility/testing functions from your original code ---
    # update_known_tcx_file_csv(
    #     source_folder=app_config.get('paths', 'source_gps_track_folder'),
    #     dest_tcx_file_list=r'C:\Users\krant\Downloads\2025_07_TCX_Files_Updated.csv',
    #     remove_duplicates_first=True
    # )

    # # Action 4: List duplicates
    # remove_duplicate_tcx_files_in_folder(source_folder=app_config.get('paths', 'source_gps_track_folder'),
    #                                      list_dont_delete=False)


    print("\nScript finished.")
