# main.py - Part 1: Imports

import configparser
import csv
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Third-party libraries
import folium
import geojson
import geopandas as gpd
import lxml.etree as ET  # Use lxml and alias it as ET for consistency
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from shapely.geometry import LineString

# main.py - Part 2: Core Data Parsing & Fingerprinting

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


def _extract_workout_id_from_filename(path: Path) -> Optional[str]:
    """Extracts the workout ID from a filename like '... (W12345).tcx'."""
    # This pattern looks for '(W' followed by digits, and captures the digits.
    match = re.search(r'\(W(\d+)\)', path.name)
    if match:
        return match.group(1)
    return None

# main.py - Part 3: Web Automation & File Handling

def get_downloads_folder() -> Path:
    """
    Returns the path to the user's Downloads folder as a Path object.

    Returns:
        Path: A pathlib.Path object representing the Downloads folder.
    """
    return Path.home() / "Downloads"


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


# main.py - Part 4: CSV Management & Data Merging

def read_known_tcx_file_csv(tcx_file_list: str) -> List[Dict[str, str]]:
    """
    Reads a list of known TCX files and their attributes from a CSV file.

    Args:
        tcx_file_list: Filespec for CSV file to read.

    Returns:
        A list of dictionaries, one for each row in the CSV.
    """
    if not Path(tcx_file_list).exists():
        print(f"INFO: Master CSV file '{Path(tcx_file_list).name}' not found. A new one will be created.")
        return []

    known_workouts = []
    try:
        # Use utf-8-sig to handle the BOM (Byte Order Mark) that Excel sometimes adds
        with open(tcx_file_list, 'r', newline='', encoding='utf-8-sig') as csvfile:
            reader = csv.DictReader(csvfile)
            for workout in reader:
                known_workouts.append(workout)
    except Exception as e:
        print(f"ERROR: Could not read master CSV file '{tcx_file_list}'. Reason: {e}")

    return known_workouts


def write_tcx_file_prop_list_to_csv(row_dicts: List[Dict[str, str]], dest_tcx_file_list: str):
    """
    Creates a CSV file with the properties of the specified TCX file list attributes.

    Args:
        row_dicts: A list of dictionaries, each containing TCX file properties.
        dest_tcx_file_list: Filespec for the destination CSV file.
    """
    if not row_dicts:
        print("No data to write to CSV.")
        return

    # Dynamically get all possible fieldnames from the data to ensure no data is lost
    all_keys: Set[str] = set()
    for row in row_dicts:
        all_keys.update(row.keys())

    # Define a preferred order for the main columns to make the CSV more readable
    preferred_order = [
        'Date Submitted', 'Workout Date', 'Activity Type', 'Link', 'Filename',
        'Fingerprint', 'TCX Activity ID', 'Avg Heart Rate', 'Avg Pace (min/km)',
        'Avg Speed (km/h)', 'Calories Burned (kCal)', 'Distance (km)',
        'Max Pace (min/km)', 'Max Speed (km/h)', 'Notes', 'Source', 'Steps',
        'Workout Time (seconds)'
    ]
    # Create a sorted list of headers with the preferred ones first
    fieldnames = sorted(list(all_keys),
                        key=lambda x: preferred_order.index(x) if x in preferred_order else len(preferred_order))

    try:
        with open(dest_tcx_file_list, 'w', encoding='UTF8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(row_dicts)
        print(f"Successfully wrote {len(row_dicts)} records to '{Path(dest_tcx_file_list).name}'.")
    except IOError as e:
        print(f"ERROR: Could not write to CSV file '{dest_tcx_file_list}'. Reason: {e}")


def _validate_and_deduplicate_workouts(workouts: List[Dict]) -> Tuple[List[Dict], bool]:
    """
    Validates a list of workouts from the MapMyRide CSV for duplicate workout IDs.

    This function is crucial for handling cases where the export from MapMyRide
    contains multiple entries for the same workout. It identifies duplicates
    based on the workout ID extracted from the 'Link' field and keeps only the
    most recent entry (based on 'Date Submitted').

    Args:
        workouts: The list of workout dictionaries read from the online CSV.

    Returns:
        A tuple containing:
        - A cleaned list of workouts with duplicates removed.
        - A boolean flag indicating if any duplicates were found and removed.
    """
    print(f"Validating {len(workouts)} workouts for duplicate IDs...")
    seen_ids: Dict[str, Dict] = {}
    duplicates_found = False

    for workout in workouts:
        workout_id = _get_workout_id_from_link(workout.get('Link', ''))
        if not workout_id:
            continue  # Skip workouts with no valid link/ID

        if workout_id in seen_ids:
            duplicates_found = True
            # Compare submission dates to keep the newest entry
            try:
                existing_date = _parse_csv_date_str(seen_ids[workout_id].get('Date Submitted', ''))
                current_date = _parse_csv_date_str(workout.get('Date Submitted', ''))
                if current_date and (not existing_date or current_date > existing_date):
                    seen_ids[workout_id] = workout  # Replace with the newer entry
            except (ValueError, TypeError):
                # If dates are malformed, just keep the first one seen
                continue
        else:
            seen_ids[workout_id] = workout

    if duplicates_found:
        print("Found and removed duplicate workout entries from the online data.")

    return list(seen_ids.values()), duplicates_found


def _merge_and_update_workout_lists(
        online_workouts: List[Dict],
        local_workouts: List[Dict]
) -> List[Dict]:
    """
    Merges the list of workouts from online with the local master list.

    This function uses the workout ID as the primary key for merging. It updates
    existing local records with any new data from the online source, and adds
    any new workouts that are not present in the local list.

    Args:
        online_workouts: A list of workout dictionaries from the online CSV.
        local_workouts: A list of workout dictionaries from the local `pk_workouts.csv`.

    Returns:
        A new, merged list of workout dictionaries.
    """
    print("Merging online data with local master list...")
    # Create a dictionary of local workouts keyed by their workout ID for efficient lookup
    local_map: Dict[str, Dict] = {
        _get_workout_id_from_link(w.get('Link', '')): w for w in local_workouts
    }
    merged_workouts: Dict[str, Dict] = local_map.copy()
    new_workouts_count = 0

    for online_workout in online_workouts:
        workout_id = _get_workout_id_from_link(online_workout.get('Link', ''))
        if not workout_id:
            continue

        if workout_id in merged_workouts:
            # If the workout already exists, update it with the latest online data
            merged_workouts[workout_id].update(online_workout)
        else:
            # If it's a new workout, add it to our merged list
            merged_workouts[workout_id] = online_workout
            new_workouts_count += 1

    if new_workouts_count > 0:
        print(f"Found {new_workouts_count} new workouts to add to the master list.")
    else:
        print("No new workouts found. Updating existing records.")

    # Return a sorted list for consistent output, sorting by date
    return sorted(
        list(merged_workouts.values()),
        key=lambda w: _parse_csv_date_str(w.get('Workout Date', '')) or datetime.min,
        reverse=True
    )


# main.py - Part 5: Duplicate Detection & Cleanup Utilities

def _scan_folder_and_build_id_map(source_folder: Path) -> Dict[str, Path]:
    """
    Scans the source folder, builds a map of workout IDs to file paths,
    and cleans up any duplicate files for the same workout ID.

    This is now the primary mechanism for detecting duplicates. It finds all
    files with the same (W...) ID and keeps only the first one, deleting
    the others.

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
            id_to_files.setdefault(workout_id, []).append(tcx_path)

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
                    file_to_delete.unlink()
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


def cleanup_numeric_suffixes(config: configparser.ConfigParser, dry_run: bool = True):
    """
    Scans the source folder and removes numeric suffixes like `_0001`.

    This utility handles two cases:
    1. Misplaced suffixes before a workout ID (e.g., `..._0001 (W123).tcx`)
    2. Trailing suffixes at the very end of the filename (e.g., `... (W123)_0001.tcx`)

    The patterns are specifically targeted to `_000[1-9]` to avoid
    accidentally removing numbers that might be years (e.g., `_2023`).

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


# main.py - Part 6: Main Synchronization Logic

def _process_workouts(
        workouts_to_process: List[Dict],
        config: configparser.ConfigParser,
        browser: webdriver.Chrome,
        temp_download_dir: Path,
        existing_files_map: Dict[str, Path]
) -> List[Dict]:
    """
    Processes a list of workouts: downloads TCX files if needed, renames them,
    and updates their metadata.

    This is the main worker function called by `export_tcx_files`.

    Args:
        workouts_to_process: A list of workout dictionaries to process.
        config: The application configuration object.
        browser: The active Selenium browser instance.
        temp_download_dir: The directory for temporary downloads.
        existing_files_map: A map of workout IDs to their existing file paths.

    Returns:
        An updated list of workout dictionaries with new filenames and fingerprints.
    """
    source_folder = Path(config.get('paths', 'source_gps_track_folder'))
    tcx_export_url_template = config.get('urls', 'tcx_export_url_template')
    updated_workouts = []
    total = len(workouts_to_process)

    for i, workout_data in enumerate(workouts_to_process):
        print(f"\n--- Processing workout {i + 1}/{total} ---")
        workout_id = _get_workout_id_from_link(workout_data.get('Link', ''))
        if not workout_id:
            print("  - ⚠️ SKIPPING: No workout ID found in link.")
            updated_workouts.append(workout_data)
            continue

        print(f"  - Workout ID: {workout_id}")
        final_tcx_path = None

        # --- Step 1: Check if a file for this workout ID already exists ---
        if workout_id in existing_files_map:
            final_tcx_path = existing_files_map[workout_id]
            print(f"  - ✅ FOUND: File already exists at '{final_tcx_path.name}'. Skipping download.")
        else:
            # --- Step 2: Download the file if it doesn't exist ---
            print("  - ⬇️ DOWNLOADING: No existing file found for this ID.")
            download_url = tcx_export_url_template.format(workout_id=workout_id)
            downloaded_path = _download_file_and_wait(browser, download_url, temp_download_dir)

            if not downloaded_path:
                print(f"  - ❌ FAILED: Could not download TCX for workout ID {workout_id}.")
                updated_workouts.append(workout_data)
                continue

            # --- Step 3: Create a new, clean filename ---
            date_prefix = _format_date_for_filename(workout_data.get('Workout Date', ''))
            activity_type = workout_data.get('Activity Type', 'Workout').replace('/', '_')
            distance_km = float(workout_data.get('Distance (km)', 0))

            # Sanitize the notes to create a clean stem
            notes = workout_data.get('Notes', '')
            # Remove invalid filename characters and shorten
            cleaned_stem = re.sub(r'[<>:"/\\|?*]', '', notes).strip()
            cleaned_stem = (cleaned_stem[:50] + '..') if len(cleaned_stem) > 50 else cleaned_stem

            new_filename_stem = f"{date_prefix} {cleaned_stem} {distance_km:.2f}km {activity_type} (W{workout_id})"
            final_tcx_path = _get_unique_filepath(source_folder, f"{new_filename_stem}.tcx")

            # --- Step 4: Move the downloaded file to its final destination ---
            try:
                shutil.move(downloaded_path, final_tcx_path)
                print(f"  - 💾 SAVED: Renamed and moved to '{final_tcx_path.name}'")
            except (OSError, shutil.Error) as e:
                print(f"  - ❌ FAILED: Could not move file. Reason: {e}")
                updated_workouts.append(workout_data)
                continue

        # --- Step 5: Update the workout record with the final file path and fingerprint ---
        if final_tcx_path and final_tcx_path.exists():
            workout_data['Filename'] = str(final_tcx_path)
            # Always get the latest fingerprint directly from the file to self-heal any drift
            authoritative_fp = get_fingerprint_from_tcx_file(final_tcx_path, silent=True)
            if authoritative_fp:
                if workout_data.get('Fingerprint') != authoritative_fp:
                    print(
                        f"  - 🔄 UPDATING FINGERPRINT: Old: {workout_data.get('Fingerprint')}, New: {authoritative_fp}")
                    workout_data['Fingerprint'] = authoritative_fp
                else:
                    print(f"  - 👍 Fingerprint is up-to-date: {authoritative_fp}")
            else:
                print("  - ⚠️ WARNING: Could not generate fingerprint for the file.")
        else:
            print("  - ⚠️ WARNING: Final TCX path not found, cannot update record.")

        updated_workouts.append(workout_data)

    return updated_workouts


def export_tcx_files(config: configparser.ConfigParser, use_local_csv: bool = False):
    """
    Orchestrates the entire process of syncing workouts from MapMyRide.

    - Cleans up local file duplicates based on workout ID.
    - Fetches the latest workout list (either online or from a local file).
    - Merges it with the existing master list.
    - Processes each workout, downloading new TCX files only when necessary.
    - Updates the master CSV with the latest data, filenames, and fingerprints.

    Args:
        config: The application configuration object.
        use_local_csv: If True, uses a local CSV for workout data instead of
                       downloading from MapMyRide. Useful for debugging.
    """
    print("\n--- STARTING WORKOUT SYNCHRONIZATION ---")

    # --- Setup Phase ---
    source_folder = Path(config.get('paths', 'source_gps_track_folder'))
    master_csv_path = Path(config.get('paths', 'tcx_file_list'))
    local_csv_path = Path(config.get('debugging', 'local_csv_path'))
    csv_export_url = config.get('urls', 'csv_export_url')

    source_folder.mkdir(parents=True, exist_ok=True)
    temp_download_dir = get_downloads_folder() / f"sel_map_extract_temp_{int(time.time())}"

    # --- Step 1: Clean up existing files and build a map of what we have ---
    existing_files_map = _scan_folder_and_build_id_map(source_folder)

    online_workouts = []
    browser = None
    try:
        # --- Step 2: Get the latest list of workouts ---
        if use_local_csv:
            print(f"\n--- Using local CSV: {local_csv_path.name} ---")
            if not local_csv_path.exists():
                print(f"FATAL: Local CSV file not found at '{local_csv_path}'.")
                return
            online_workouts = read_known_tcx_file_csv(str(local_csv_path))
        else:
            print("\n--- Fetching latest workout list from MapMyRide ---")
            temp_download_dir.mkdir(parents=True, exist_ok=True)
            chrome_options = ChromeOptions()
            prefs = {"download.default_directory": str(temp_download_dir)}
            chrome_options.add_experimental_option("prefs", prefs)
            browser = webdriver.Chrome(options=chrome_options)

            if not _login_to_mapmyride(browser, config):
                print("FATAL: Login failed. Aborting sync.")
                return

            downloaded_csv_path = _download_file_and_wait(browser, csv_export_url, temp_download_dir)
            if not downloaded_csv_path:
                print("FATAL: Failed to download the workout CSV. Aborting sync.")
                return

            shutil.copy(downloaded_csv_path, local_csv_path)
            print(f"Downloaded and backed up online CSV to '{local_csv_path.name}'")
            online_workouts = read_known_tcx_file_csv(str(downloaded_csv_path))

        # --- Step 3: Merge and Process ---
        if not online_workouts:
            print("No workouts found in the source CSV. Nothing to do.")
            return

        online_workouts, _ = _validate_and_deduplicate_workouts(online_workouts)
        local_workouts = read_known_tcx_file_csv(str(master_csv_path))
        workouts_to_process = _merge_and_update_workout_lists(online_workouts, local_workouts)

        # The browser is only needed if we are not in local mode
        if not use_local_csv and browser:
            final_workouts = _process_workouts(
                workouts_to_process, config, browser, temp_download_dir, existing_files_map
            )
            # --- Step 4: Finalize and Save ---
            write_tcx_file_prop_list_to_csv(final_workouts, str(master_csv_path))
            print("\n✅ Synchronization complete.")
        elif use_local_csv:
            print("\n--- LOCAL MODE: Skipping TCX processing. ---")
            print("To download files, run with use_local_csv=False.")
            write_tcx_file_prop_list_to_csv(workouts_to_process, str(master_csv_path))
            print("\n✅ Merging complete.")

    finally:
        # --- Cleanup Phase ---
        if browser:
            browser.quit()
        if temp_download_dir.exists():
            shutil.rmtree(temp_download_dir)
            print(f"Cleaned up temporary directory: {temp_download_dir}")


# main.py - Part 7: Map Generation & Visualization

def create_simplified_geojson_from_tcx_geopandas(
        tcx_file: str,
        geojson_file: str,
        tolerance: float = 10.0
):
    """
    Creates a simplified GeoJSON file from a TCX file using GeoPandas.

    This function reads the track points from a TCX file, simplifies the resulting
    line geometry to reduce the number of points, and saves it as a GeoJSON file.

    Args:
        tcx_file: The path to the input TCX file.
        geojson_file: The path for the output GeoJSON file.
        tolerance: The tolerance for the simplification algorithm, in meters.
                   A larger value results in a more simplified line.
    """
    try:
        # Use a helper to parse coordinates, which is more robust
        coordinates = parse_tcx(tcx_file)
        if len(coordinates) < 2:
            print(f"  - Skipping {Path(tcx_file).name}: not enough points to form a line.")
            return

        line = LineString(coordinates)
        # Create a GeoDataFrame with the correct CRS for geographic coordinates
        gdf = gpd.GeoDataFrame([1], geometry=[line], crs="EPSG:4326")

        # To use a tolerance in meters, we need to project to a suitable CRS.
        # UTM is a good choice. We can find the appropriate UTM zone from the centroid.
        centroid = gdf.geometry.unary_union.centroid
        utm_crs = gdf.estimate_utm_crs(datum_name="WGS 84")

        # Project, simplify, and then project back to the original CRS
        gdf_projected = gdf.to_crs(utm_crs)
        gdf_projected['geometry'] = gdf_projected.geometry.simplify(
            tolerance=tolerance, preserve_topology=True
        )
        gdf_simplified = gdf_projected.to_crs(gdf.crs)

        # Save the simplified geometry to a GeoJSON file
        gdf_simplified.to_file(geojson_file, driver='GeoJSON')
        print(f"  - Simplified '{Path(tcx_file).name}' -> '{Path(geojson_file).name}'")

    except Exception as e:
        print(f"  - ❌ ERROR simplifying '{Path(tcx_file).name}': {e}")


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
        workouts: List[Dict[str, str]] = read_known_tcx_file_csv(tcx_file_list=tcx_file_list)

        created_count = 0
        skipped_count = 0
        error_count = 0

        walk_hike_workouts = [w for w in workouts if w.get('Activity Type', '') in ('Walk', 'Hike')]
        print(f"Found {len(walk_hike_workouts)} Walk/Hike workouts to process.")

        for workout in walk_hike_workouts:
            original_filepath_str = workout.get('Filename')
            if not original_filepath_str:
                continue

            original_filepath = Path(original_filepath_str)
            # The source path should be based on the original filename, not a reconstructed one
            src_path = original_filepath
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
                    print(f"  - ⚠️ WARNING: Source file not found, cannot simplify: {src_path}")
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


def create_tcx_route_map(config: configparser.ConfigParser):
    """
    Creates an HTML map visualizing all simplified GeoJSON routes.
    """
    print("\n--- Creating HTML Route Map ---")
    simplified_folder = Path(config.get('paths', 'simplified_gps_track_folder'))
    project_path = Path(config.get('paths', 'project_path'))
    map_file_path = project_path / "all_routes.html"

    if not simplified_folder.is_dir():
        print(f"ERROR: Simplified tracks folder not found at '{simplified_folder}'.")
        print("Please run the 'simplify_all_tcx_files' function first.")
        return

    geojson_files = list(simplified_folder.glob("*.geojson"))
    if not geojson_files:
        print("No GeoJSON files found to map.")
        return

    print(f"Found {len(geojson_files)} GeoJSON files to add to the map.")

    # Create a map centered on a default location (e.g., Perth)
    # This will be automatically adjusted by fit_bounds later.
    m = folium.Map(location=[-31.95, 115.86], zoom_start=10)

    # Create a FeatureGroup to hold all the routes
    feature_group = folium.FeatureGroup(name="All Routes")

    for geojson_file in geojson_files:
        try:
            # Add each GeoJSON file to the feature group
            folium.GeoJson(
                str(geojson_file),
                style_function=lambda x: {'color': 'blue', 'weight': 2.5, 'opacity': 0.7}
            ).add_to(feature_group)
        except Exception as e:
            print(f"  - Could not process GeoJSON file '{geojson_file.name}': {e}")

    # Add the feature group to the map
    feature_group.add_to(m)

    # Automatically adjust the map bounds to fit all the routes
    m.fit_bounds(feature_group.get_bounds())

    # Save the map to an HTML file
    try:
        m.save(str(map_file_path))
        print(f"\n✅ Successfully created map: '{map_file_path}'")
    except Exception as e:
        print(f"\n❌ ERROR: Could not save the map file. Reason: {e}")


# main.py - Part 8: Main Execution Block

def main():
    """
    Main function to run the desired actions based on the configuration.
    """
    # Load the application configuration from the INI file
    app_config = configparser.ConfigParser()
    config_path = 'config.ini'
    if not Path(config_path).exists():
        print(f"FATAL: Configuration file '{config_path}' not found.")
        print("Please ensure the config file is in the same directory as the script.")
        return
    app_config.read(config_path)

    # --- CHOOSE WHICH ACTION TO RUN ---
    # Uncomment the function you wish to execute.

    # --------------------------------------------------------------------------
    # Action 1: Full Synchronization (Online Mode)
    # This is the main function. It will:
    # - Clean up local file duplicates by workout ID.
    # - Download the latest workout list from MapMyRide.
    # - Merge with your master list.
    # - Download only the TCX files that are missing.
    # - Update pk_workouts.csv with the latest data.
    # --------------------------------------------------------------------------
    export_tcx_files(config=app_config, use_local_csv=False)

    # --------------------------------------------------------------------------
    # Action 2: Sync using Local Backup (Offline Mode)
    # Use this for debugging or if you want to re-process without
    # hitting the MapMyRide servers. It uses 'mapmyride_export.csv'.
    # --------------------------------------------------------------------------
    # export_tcx_files(config=app_config, use_local_csv=True)

    # --------------------------------------------------------------------------
    # Action 3: Generate GeoJSON and HTML Map
    # These functions are for visualization. Run them after a sync.
    # --------------------------------------------------------------------------
    # Step 3a: Create simplified GeoJSON files for all Walk/Hike workouts.
    # By default, this only creates missing files. To overwrite all, use only_if_missing=False.
    # simplify_all_tcx_files(config=app_config, only_if_missing=True)

    # Step 3b: Create the final HTML map from the GeoJSON files.
    # create_tcx_route_map(config=app_config)

    # --------------------------------------------------------------------------
    # Utility & Diagnostic Functions
    # --------------------------------------------------------------------------
    # Utility to test your MapMyRide login credentials.
    # login_only(config=app_config)

    # Utility to clean up old numeric suffixes like `_0001` from filenames.
    # cleanup_numeric_suffixes(config=app_config, dry_run=False)

    # Diagnostic tool to find TCX files on disk that are not in pk_workouts.csv.
    # find_unreferenced_tcx_files(config=app_config)


if __name__ == "__main__":
    main()
