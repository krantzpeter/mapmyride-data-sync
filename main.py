# C:/Users/krant/PycharmProjects/SelMapExtract/main.py

import configparser
import csv
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Optional
import PySimpleGUI as sg
import sys

from client import MapMyRideClient
from repository import WorkoutRepository
from workout import Workout
from map_generator import MapGenerator

log = logging.getLogger(__name__)


def _process_and_merge_workouts(online_data: List[Dict],
                                repo: WorkoutRepository,
                                existing_files_map: Dict[str, Dict],
                                client: Optional[MapMyRideClient] = None,
                                full_check: bool = True):
    log.info("--- Processing and Merging Workouts ---")
    new_workouts_count = 0
    repaired_names_count = 0

    for i, row in enumerate(online_data):
        temp_workout = Workout(row)
        if not temp_workout.workout_id:
            continue

        # SKIP EMPTY WORKOUTS (0 distance and 0 time)
        if temp_workout.is_empty:
            log.info(f"  - SKIPPING: Workout ID {temp_workout.workout_id} is empty (0km/0s).")
            continue

        existing_workout = repo.get_by_id(temp_workout.workout_id)
        is_hike_or_walk = any(t in temp_workout.activity_type.lower() for t in ['hike', 'walk'])

        if not existing_workout:
            new_workouts_count += 1
            log.info(f"  - NEW: Workout ID {temp_workout.workout_id} ({temp_workout.activity_type})")
            new_workout = Workout(row)

            if client and is_hike_or_walk:
                name = client.fetch_workout_name(new_workout.workout_id)
                if name:
                    new_workout.temp_proper_name = name
                    log.info(f"    - Title recovered: {name}")

            if client:
                temp_path = client.download_tcx_file(new_workout.workout_id)
                if temp_path:
                    new_workout.tcx_path = repo.save_tcx_file(temp_path, new_workout)

            repo.add_or_update(new_workout)
        else:
            if full_check:
                existing_workout.update_from_online_data(row)
                file_status = existing_files_map.get(existing_workout.workout_id, {})

                # Check disk status
                if file_status:
                    existing_workout.tcx_path = file_status.get('path')
                    if file_status.get('title') and not existing_workout.workout_name:
                        existing_workout.temp_proper_name = file_status['title']

                is_managed = file_status.get('is_standard', False)
                file_missing = not existing_workout.tcx_path or not existing_workout.tcx_path.exists()

                # 1. Scraping Repair (Hike/Walk only)
                if is_hike_or_walk and not existing_workout.workout_name and not is_managed and client:
                    name = client.fetch_workout_name(existing_workout.workout_id)
                    if name:
                        existing_workout.temp_proper_name = name
                        repaired_names_count += 1
                        log.info(f"    - 🛠 REPAIRED: Name recovered: {name}")
                        if not file_missing:
                            existing_workout.tcx_path = repo.save_tcx_file(existing_workout.tcx_path,
                                                                           existing_workout,
                                                                           ignore_if_exists=True)

                # 2. Missing File Repair: Download if record exists but file is gone
                if file_missing and client:
                    log.info(f"    - 🛠 RE-DOWNLOADING: File missing for ID {existing_workout.workout_id}")
                    temp_path = client.download_tcx_file(existing_workout.workout_id)
                    if temp_path:
                        existing_workout.tcx_path = repo.save_tcx_file(temp_path, existing_workout)

                repo.add_or_update(existing_workout)

    log.info(f"--- Sync Summary: {new_workouts_count} New, {repaired_names_count} Repaired ---")


def repair_workout_names(config: configparser.ConfigParser,
                         client: Optional[MapMyRideClient] = None,
                         workout_ids: Optional[List[str]] = None,
                         fix_all_activities: bool = False,
                         dry_run: bool = False):
    """
    Recovers 'Proper Names' for workouts by scraping the MapMyRide website.

    Args:
        config: App configuration.
        client: Selenium client.
        workout_ids: List of IDs to process.
        fix_all_activities: If True, bypasses the Hike/Walk filter.
        dry_run: If True, logs intended changes without renaming files or saving CSV.
    """
    repo = WorkoutRepository(config)
    repo.load()

    # 1. Scan disk to refresh paths and titles (Runtime Recovery)
    existing_files_map = repo.scan_and_build_id_map()
    for w in repo.get_all():
        if w.workout_id in existing_files_map:
            info = existing_files_map[w.workout_id]
            w.tcx_path = info['path']
            # If we recovered a title from the filename and Workout Name is empty, use it
            if info['title'] and not w.workout_name:
                w.temp_proper_name = info['title']

    # 2. Determine which workouts to process
    if workout_ids:
        to_repair = [repo.get_by_id(wid) for wid in workout_ids if repo.get_by_id(wid)]
    else:
        to_repair = [w for w in repo.get_all() if not w.workout_name and (
                fix_all_activities or any(t in w.activity_type.lower() for t in ['hike', 'walk']))]

    if not to_repair:
        log.info("No workouts identified for name repair.")
        return

    mode_prefix = "[DRY RUN] " if dry_run else ""
    log.info(f"--- {mode_prefix}Starting Name Repair for {len(to_repair)} workouts ---")

    # 3. Initialize a single client for the entire batch
    own_client = False
    if client is None:
        client = MapMyRideClient(config)
        own_client = True

    try:
        for i, workout in enumerate(to_repair):
            log.info(f"[{i + 1}/{len(to_repair)}] {mode_prefix}Processing {workout.workout_id}...")

            # Scrape is always needed to get the authoritative 'Proper Name' from MMR
            name = client.fetch_workout_name(workout.workout_id)

            if name:
                # Update memory for filename generation test
                # (Workout.workout_name prioritizes temp_proper_name)
                old_temp_name = workout.temp_proper_name
                workout.temp_proper_name = name

                if workout.tcx_path and workout.tcx_path.exists():
                    old_filename = workout.tcx_path.name
                    new_filename = f"{workout.generate_filename_stem()}.tcx"

                    if old_filename != new_filename:
                        log.info(f"  - {mode_prefix}ACTION: Would rename '{old_filename}'")
                        log.info(f"    -> TO: '{new_filename}'")
                        if not dry_run:
                            new_path = repo.save_tcx_file(workout.tcx_path, workout, ignore_if_exists=True)
                            if new_path:
                                workout.tcx_path = new_path
                    else:
                        log.info(f"  - {mode_prefix}NO CHANGE: Filename matches recovered name.")

                # If dry run, revert the memory change to keep the object clean
                if dry_run:
                    workout.temp_proper_name = old_temp_name
            else:
                log.warning(f"  - {mode_prefix}FAILED: Could not recover name for {workout.workout_id}")

        # 4. Save metadata changes to CSV if not in dry run
        if not dry_run:
            repo.save_all()
            log.info("✅ All changes saved to master list.")
        else:
            log.info(f"--- {mode_prefix}Finished. No files or CSV records were modified. ---")

    finally:
        if own_client:
            # Shutdown the single browser instance
            client.__exit__(None, None, None)


def sync_workouts(config, use_local_csv=False, full_check=False):
    """Orchestrates the synchronization process."""
    repo = WorkoutRepository(config)
    repo.load()

    # Scan disk to find existing files and recover titles from filenames
    existing_files_map = repo.scan_and_build_id_map()

    online_data = []
    client = None

    try:
        if use_local_csv:
            log.info("Using local CSV for synchronization.")
            with open(config.get('debugging', 'local_csv_path'), 'r', encoding='utf-8-sig') as f:
                online_data = list(csv.DictReader(f))
        else:
            client = MapMyRideClient(config)
            online_data_path = client.download_workout_list_csv()
            if online_data_path:
                with open(online_data_path, 'r', encoding='utf-8-sig') as f:
                    online_data = list(csv.DictReader(f))

        if online_data:
            _process_and_merge_workouts(online_data, repo, existing_files_map, client, full_check)
            repo.save_all()
            log.info("✅ Synchronization complete.")
    finally:
        if client:
            client.__exit__(None, None, None)


def simplify_only(config: configparser.ConfigParser):
    """Updates the 'Simplified' folder for GPXSee without generating the HTML map."""
    log.info("--- STEP 2: UPDATING SIMPLIFIED TRACKS FOR GPXSEE ---")
    repo = WorkoutRepository(config)
    repo.load()

    # We must scan disk here too to ensure workout_name is recovered for the GeoJSON
    existing_files_map = repo.scan_and_build_id_map()
    all_workouts = repo.get_all()

    for w in all_workouts:
        if w.workout_id in existing_files_map:
            info = existing_files_map[w.workout_id]
            if info['title'] and not w.workout_name:
                w.temp_proper_name = info['title']

    if not all_workouts:
        log.warning("  ! No workouts found to process.")
        return

    log.info(f"  > Processing {len(all_workouts)} workouts...")
    map_gen = MapGenerator(config)
    map_gen.simplify_workouts(all_workouts, workout_types={'walk', 'hike'}, only_if_missing=True)
    log.info("✅ Simplified folder update complete.")


def generate_maps(config: configparser.ConfigParser):
    """Full regeneration: Simplifies tracks AND builds the HTML interactive map."""
    log.info("--- STARTING FULL MAP REGENERATION ---")
    simplify_only(config)

    log.info("--- STEP 3: GENERATING INTERACTIVE HTML DASHBOARD ---")
    map_gen = MapGenerator(config)
    log.info("  > Aggregating GeoJSON data and rendering all_routes.html...")
    map_gen.create_route_map()
    log.info("✅ Full HTML map generation complete.")


def main():
    """Main function to run the PySimpleGUI event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )

    app_config = configparser.ConfigParser()
    config_path = 'config.ini'
    if not Path(config_path).exists():
        sg.popup_error(f"FATAL: Configuration file '{config_path}' not found.")
        return
    app_config.read(config_path)

    # # --- ONE-TIME SURGICAL REPAIR HOOK ---
    # # This block processes the specific list of IDs provided.
    # # You can comment this block out or remove it after one successful run.
    # ids_to_fix = [
    #     '8782629674', '8778241216', '8774605240', '8755448943',
    #     '8754095487', '8737993344', '8727597018', '8725460749',
    #     '8724063734', '8723676990', '8721711543', '8720197148',
    #     '8691926181', '8679932016', '8675744877', '8675744082',
    #     '8666511699', '8664620348', '8658414670', '8653715850',
    #     '8649434867', '8645074345', '8635432315', '8631067667'
    # ]
    #
    # log.info(f"--- STARTING SURGICAL REPAIR FOR {len(ids_to_fix)} WORKOUTS ---")
    # # fix_all_activities=True ensures that even bike rides in this specific list are fixed.
    # # Set dry_run=True to verify names before applying
    # repair_workout_names(app_config, workout_ids=ids_to_fix, fix_all_activities=True, dry_run=False)
    # log.info("--- SURGICAL REPAIR COMPLETE ---")
    # # -------------------------------------

    sg.ChangeLookAndFeel('SystemDefault')
    action_buttons = ['-QUICK-', '-FULL-', '-LOCAL-', '-MAPS-']
    layout = [
        [sg.Text('MapMyRide Data Sync & Mapping Tool', font=('Helvetica', 16))],
        [sg.Button('Quick Sync', key='-QUICK-', size=(20, 2))],
        [sg.Button('Full Sync', key='-FULL-', size=(20, 2))],
        [sg.Button('Sync from Local CSV', key='-LOCAL-', size=(20, 2))],
        [sg.Button('Generate Maps', key='-MAPS-', size=(20, 2))],
        [sg.Output(size=(80, 20), key='-OUTPUT-')],
        [sg.Button('Exit', size=(10, 1))]
    ]

    window = sg.Window('MapMyRide Control Panel', layout)

    def toggle_buttons(disabled: bool):
        for key in action_buttons:
            window[key].update(disabled=disabled)

    while True:
        event, values = window.read()
        if event == sg.WIN_CLOSED or event == 'Exit':
            break

        if event in action_buttons:
            toggle_buttons(disabled=True)
            window.refresh()
            window['-OUTPUT-'].update('')

            try:
                if event == '-QUICK-':
                    sync_workouts(config=app_config, use_local_csv=False, full_check=False)
                    simplify_only(config=app_config)
                elif event == '-FULL-':
                    sync_workouts(config=app_config, use_local_csv=False, full_check=True)
                    simplify_only(config=app_config)
                elif event == '-LOCAL-':
                    sync_workouts(config=app_config, use_local_csv=True, full_check=False)
                    simplify_only(config=app_config)
                elif event == '-MAPS-':
                    generate_maps(config=app_config)
            except Exception as e:
                log.exception("An unhandled exception occurred during operation.")
            finally:
                toggle_buttons(disabled=False)

    window.close()


if __name__ == "__main__":
    main()
