# C:/Users/krant/PycharmProjects/SelMapExtract/main.py

import configparser
import csv
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
import PySimpleGUI as sg
import sys

from client import MapMyRideClient
from repository import WorkoutRepository
from workout import Workout
from map_generator import MapGenerator

# Module level logger
log = logging.getLogger(__name__)


class GUIHandler(logging.Handler):
    """
    Custom logging handler to mirror log messages into a PySimpleGUI Multiline element.
    Provides immediate GUI refresh to show progress in real-time.
    """

    def __init__(self, window: sg.Window, key: str):
        super().__init__()
        self.window = window
        self.key = key

    def emit(self, record):
        try:
            msg = self.format(record)
            # Use the window's thread-safe print method
            self.window[self.key].print(msg)
            # Force the GUI to update immediately so the user sees progress
            self.window.refresh()
        except Exception:
            self.handleError(record)


def _process_and_merge_workouts(online_data: List[Dict],
                                repo: WorkoutRepository,
                                existing_files_map: Dict[str, Dict[str, Any]],
                                client: Optional[MapMyRideClient] = None,
                                full_check: bool = True):
    log.info("--- Processing and Merging Workouts ---")
    new_workouts_count = 0
    repaired_names_count = 0

    for i, row in enumerate(online_data):
        temp_workout = Workout(row)
        w_id = temp_workout.workout_id
        if not w_id:
            continue

        if temp_workout.is_empty:
            log.info(f"  - SKIPPING: Workout ID {w_id} is empty (0km/0s).")
            continue

        existing_workout = repo.get_by_id(w_id)
        is_hike_or_walk = any(t in temp_workout.activity_type.lower() for t in ['hike', 'walk'])

        if existing_workout is None:
            new_workouts_count += 1
            log.info(f"  - NEW: Workout ID {w_id} ({temp_workout.activity_type})")
            new_workout = Workout(row)

            if client and is_hike_or_walk:
                name = client.fetch_workout_name(w_id)
                if name:
                    new_workout.temp_proper_name = name
                    log.info(f"    - Title recovered: {name}")

            if client:
                temp_path = client.download_tcx_file(w_id)
                if temp_path:
                    new_workout.tcx_path = repo.save_tcx_file(temp_path, new_workout)

            repo.add_or_update(new_workout)
        else:
            if full_check:
                existing_workout.update_from_online_data(row)
                file_status = existing_files_map.get(w_id, {})

                if file_status:
                    existing_workout.tcx_path = file_status.get('path')
                    if file_status.get('title') and not existing_workout.workout_name:
                        existing_workout.temp_proper_name = file_status['title']

                is_managed = file_status.get('is_standard', False)
                current_path = existing_workout.tcx_path
                file_missing = current_path is None or not current_path.exists()

                if is_hike_or_walk and not existing_workout.workout_name and not is_managed and client:
                    scrape_name = client.fetch_workout_name(w_id)
                    if scrape_name:
                        existing_workout.temp_proper_name = scrape_name
                        repaired_names_count += 1
                        log.info(f"    - 🛠 REPAIRED: Name recovered: {scrape_name}")
                        if not file_missing and current_path is not None:
                            existing_workout.tcx_path = repo.save_tcx_file(current_path,
                                                                           existing_workout,
                                                                           ignore_if_exists=True)

                if file_missing and client:
                    log.info(f"    - 🛠 RE-DOWNLOADING: File missing for ID {w_id}")
                    re_download_path = client.download_tcx_file(w_id)
                    if re_download_path:
                        existing_workout.tcx_path = repo.save_tcx_file(re_download_path, existing_workout)

                repo.add_or_update(existing_workout)

    log.info(f"--- Sync Summary: {new_workouts_count} New, {repaired_names_count} Repaired ---")


def repair_workout_names(config: configparser.ConfigParser,
                         client: Optional[MapMyRideClient] = None,
                         workout_ids: Optional[List[str]] = None,
                         fix_all_activities: bool = False,
                         dry_run: bool = False):
    repo = WorkoutRepository(config)
    repo.load()

    existing_files_map = repo.scan_and_build_id_map()
    for w in repo.get_all():
        f_info = existing_files_map.get(w.workout_id)
        if f_info:
            w.tcx_path = f_info['path']
            if f_info['title'] and not w.workout_name:
                w.temp_proper_name = f_info['title']

    if workout_ids:
        to_repair: List[Workout] = []
        for wid in workout_ids:
            found_w = repo.get_by_id(wid)
            if found_w:
                to_repair.append(found_w)
    else:
        to_repair = [w for w in repo.get_all() if not w.workout_name and (
                fix_all_activities or any(t in w.activity_type.lower() for t in ['hike', 'walk']))]

    if not to_repair:
        log.info("No workouts identified for name repair.")
        return

    mode_prefix = "[DRY RUN] " if dry_run else ""
    log.info(f"--- {mode_prefix}Starting Name Repair for {len(to_repair)} workouts ---")

    own_client = False
    if client is None:
        client = MapMyRideClient(config)
        own_client = True

    try:
        for i, workout in enumerate(to_repair):
            log.info(f"[{i + 1}/{len(to_repair)}] {mode_prefix}Processing {workout.workout_id}...")
            name = client.fetch_workout_name(workout.workout_id)

            if name:
                old_temp_name = workout.temp_proper_name
                workout.temp_proper_name = name

                w_path = workout.tcx_path
                if w_path and w_path.exists():
                    old_filename = w_path.name
                    new_filename = f"{workout.generate_filename_stem()}.tcx"

                    if old_filename != new_filename:
                        log.info(f"  - {mode_prefix}ACTION: Would rename '{old_filename}' to '{new_filename}'")
                        if not dry_run:
                            new_path = repo.save_tcx_file(w_path, workout, ignore_if_exists=True)
                            if new_path:
                                workout.tcx_path = new_path
                    else:
                        log.info(f"  - {mode_prefix}NO CHANGE: Filename matches recovered name.")

                if dry_run:
                    workout.temp_proper_name = old_temp_name
            else:
                log.warning(f"  - {mode_prefix}FAILED: Could not recover name for {workout.workout_id}")

        if not dry_run:
            repo.save_all()
            log.info("✅ All changes saved to master list.")
        else:
            log.info(f"--- {mode_prefix}Finished. No files or CSV records were modified. ---")

    finally:
        if own_client and client:
            client.__exit__(None, None, None)


def sync_workouts(config, use_local_csv=False, full_check=False):
    repo = WorkoutRepository(config)
    repo.load()
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
    log.info("--- STEP 2: UPDATING SIMPLIFIED TRACKS FOR GPXSEE ---")
    repo = WorkoutRepository(config)
    repo.load()
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
    log.info("--- STARTING FULL MAP REGENERATION ---")
    simplify_only(config)
    log.info("--- STEP 3: GENERATING INTERACTIVE HTML DASHBOARD ---")
    map_gen = MapGenerator(config)
    log.info("  > Aggregating GeoJSON data and rendering all_routes.html...")
    map_gen.create_route_map()
    log.info("✅ Full HTML map generation complete.")


def main():
    # 1. Configuration
    app_config = configparser.ConfigParser()
    config_path = 'config.ini'
    if not Path(config_path).exists():
        sg.popup_error(f"FATAL: Configuration file '{config_path}' not found.")
        return
    app_config.read(config_path)

    # 2. GUI Setup
    sg.ChangeLookAndFeel('SystemDefault')
    action_buttons = ['-QUICK-', '-FULL-', '-LOCAL-', '-MAPS-']
    layout = [
        [sg.Text('MapMyRide Data Sync & Mapping Tool', font=('Helvetica', 16))],
        [sg.Button('Quick Sync', key='-QUICK-', size=(20, 2))],
        [sg.Button('Full Sync', key='-FULL-', size=(20, 2))],
        [sg.Button('Sync from Local CSV', key='-LOCAL-', size=(20, 2))],
        [sg.Button('Generate Maps', key='-MAPS-', size=(20, 2))],
        # Removed reroute_stdout/stderr to prevent conflict with console logging
        [sg.Multiline(size=(80, 20), key='-OUTPUT-', autoscroll=True)],
        [sg.Button('Exit', size=(10, 1))]
    ]

    window = sg.Window('MapMyRide Control Panel', layout, finalize=True)

    # 3. Robust Logging Configuration
    # We add two handlers: one for the terminal and one for the GUI Multiline element
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear existing handlers to prevent duplicate messages if main is re-entered
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Console Handler (Explicitly use sys.stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # GUI Handler
    gui_handler = GUIHandler(window, '-OUTPUT-')
    gui_handler.setFormatter(formatter)
    root_logger.addHandler(gui_handler)

    def toggle_buttons(disabled: bool):
        for key in action_buttons:
            btn = window[key]
            if btn:
                btn.update(disabled=disabled)

    # 4. Event Loop
    while True:
        event, values = window.read()
        if event == sg.WIN_CLOSED or event == 'Exit':
            break

        if event in action_buttons:
            toggle_buttons(disabled=True)
            window.refresh()

            output_el = window['-OUTPUT-']
            if output_el:
                output_el.update('')

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
                log.error(f"An unhandled exception occurred: {e}", exc_info=True)
            finally:
                toggle_buttons(disabled=False)

    window.close()


if __name__ == "__main__":
    main()
