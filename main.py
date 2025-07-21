# main.py

import configparser
import csv
import shutil
from pathlib import Path
from typing import List, Dict, Optional

# --- Import our new custom classes ---
from client import MapMyRideClient
from map_generator import MapGenerator
from repository import WorkoutRepository
from workout import Workout
import PySimpleGUI as sg


def _read_online_csv_data(client: MapMyRideClient, repo: WorkoutRepository) -> Optional[List[Dict]]:
    """Uses the client to download, backup, and read the online workout CSV."""
    downloaded_csv_path = client.download_workout_list_csv()
    if not downloaded_csv_path:
        print("FATAL: Failed to download the workout CSV. Aborting sync.")
        return None

    shutil.copy(downloaded_csv_path, repo.local_csv_path)
    print(f"Downloaded and backed up online CSV to '{repo.local_csv_path.name}'")
    with open(downloaded_csv_path, 'r', newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _read_local_csv_data(repo: WorkoutRepository) -> Optional[List[Dict]]:
    """Reads the workout data from the local backup CSV."""
    print(f"\n--- Using local CSV: {repo.local_csv_path.name} ---")
    if not repo.local_csv_path.exists():
        print(f"FATAL: Local CSV file not found at '{repo.local_csv_path}'.")
        return None
    with open(repo.local_csv_path, 'r', newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _process_and_merge_workouts(
        online_workouts_data: List[Dict],
        repo: WorkoutRepository,
        existing_files_map: Dict[str, Path],
        client: Optional[MapMyRideClient] = None,
        full_check: bool = True
):
    """Processes workouts, merging them into the repository and downloading if a client is provided."""
    print("\n--- Processing and Merging Workouts ---")
    new_workouts_count = 0
    updated_fingerprint_count = 0

    for i, row in enumerate(online_workouts_data):
        print(f"\n--- Processing workout {i + 1}/{len(online_workouts_data)} ---")

        # Create a temporary workout object just to get the ID for lookup
        temp_workout = Workout(row)
        if not temp_workout.workout_id:
            print("  - ⚠️ SKIPPING: No workout ID found in link.")
            continue

        print(f"  - Workout ID: {temp_workout.workout_id}")
        existing_workout = repo.get_by_id(temp_workout.workout_id)

        if not existing_workout:
            new_workouts_count += 1
            print(f"  - NEW: Workout ID {temp_workout.workout_id} not found in local database.")

            # This is a truly new workout, so we create its object
            new_workout = Workout(row)

            # Download the TCX file only if we are in online mode (client is provided)
            if client:
                temp_path = client.download_tcx_file(new_workout.workout_id)
                if temp_path:
                    final_path = repo.save_tcx_file(temp_path, new_workout)
                    new_workout.tcx_path = final_path
            repo.add_or_update(new_workout)
        else:
            # This workout already exists in our master CSV.
            if full_check:
                # --- This is the THOROUGH path ---
                print(f"  - 🧐 VERIFYING: Workout ID {existing_workout.workout_id} exists. Performing full check.")

                # Update with the latest online data
                existing_workout.update_from_online_data(row)

                # Ensure the file path from the repo is set correctly
                if temp_workout.workout_id in existing_files_map:
                    existing_workout.tcx_path = existing_files_map[temp_workout.workout_id]

                    # main.py -> _process_and_merge_workouts() (Updated)

                    # Self-heal fingerprint using the public properties
                    authoritative_fp = existing_workout.fingerprint
                    if authoritative_fp and existing_workout.stored_fingerprint != authoritative_fp:
                        print(
                            f"  - 🔄 UPDATING FINGERPRINT: Old: {existing_workout.stored_fingerprint}, New: {authoritative_fp}")
                        updated_fingerprint_count += 1
                        # Explicitly update the fingerprint in the object's data
                        existing_workout.update_fingerprint(authoritative_fp)
                    else:
                        print(f"  - 👍 Fingerprint is up-to-date: {authoritative_fp}")

                    repo.add_or_update(existing_workout)
            else:
                # --- This is the QUICK path ---
                print(f"  - ✅ SKIPPING: Workout ID {existing_workout.workout_id} already exists. Quick sync mode.")
                pass  # Do nothing for existing workouts in quick mode

    print(f"\n--- Sync Summary ---")
    print(f"New workouts found: {new_workouts_count}")
    print(f"Fingerprints updated: {updated_fingerprint_count}")


# In main.py

def sync_workouts(config: configparser.ConfigParser, use_local_csv: bool = False, full_check: bool = True):
    """Orchestrates the entire process of syncing workouts from MapMyRide."""
    print("\n--- STARTING WORKOUT SYNCHRONIZATION ---")

    repo = WorkoutRepository(config)
    repo.load()
    existing_files_map = repo.scan_and_build_id_map()

    # The 'with' block now safely manages the client's lifecycle (especially cleanup).
    # The browser will NOT launch unless a download method is actually called.
    try:
        with MapMyRideClient(config) as client:
            online_workouts_data: Optional[List[Dict]] = None

            # This variable will hold the client instance ONLY if we are in online mode.
            # It will be passed to the processing function for downloading TCX files.
            client_for_processing: Optional[MapMyRideClient] = None

            if use_local_csv:
                online_workouts_data = _read_local_csv_data(repo)
                # In this branch, no client methods are called, so no login occurs.
            else:  # Online mode
                # This is the first call to the client, which will trigger the JIT login.
                online_workouts_data = _read_online_csv_data(client, repo)
                # If we successfully got data, we set the client to be used for processing.
                if online_workouts_data:
                    client_for_processing = client

            # Now, process the data we've loaded, either from local or online.
            if online_workouts_data:
                _process_and_merge_workouts(
                    online_workouts_data,
                    repo,
                    existing_files_map,
                    client=client_for_processing,  # Pass the active client, or None
                    full_check=full_check
                )
            else:
                print("No workout data found to process.")

    except ConnectionError as e:
        print(e)
        # The __exit__ method of the client will still be called for cleanup.
        return

    # Save all changes to the master CSV at the very end.
    repo.save_all()
    print("\n✅ Synchronization complete.")


def generate_maps(config: configparser.ConfigParser):
    """
    Generates the GeoJSON files and the final HTML map.
    """
    print("\n--- STARTING MAP GENERATION ---")
    repo = WorkoutRepository(config)
    repo.load()  # Load all workout data from the master CSV

    all_workouts = repo.get_all()
    if not all_workouts:
        print("No workouts found in the repository. Cannot generate maps.")
        return

    map_gen = MapGenerator(config)
    map_gen.simplify_workouts(all_workouts, only_if_missing=True)
    map_gen.create_route_map()
    print("\n✅ Map generation complete.")


def main():
    """
    Main function to run the desired actions based on the configuration.
    (Updated for compatibility with PySimpleGUI v4.60.4)
    """
    # --- 1. Load Config ---
    app_config = configparser.ConfigParser()
    config_path = 'config.ini'
    if not Path(config_path).exists():
        # Using popup_error which is compatible with v4
        sg.popup_error(f"FATAL: Configuration file '{config_path}' not found.")
        return
    app_config.read(config_path)

    # --- 2. Define the GUI Layout ---
    # CHANGE 1: Use the older 'ChangeLookAndFeel' method instead of 'theme'
    sg.ChangeLookAndFeel('SystemDefault')

    action_buttons = ['-QUICK-', '-FULL-', '-LOCAL-', '-MAPS-']
    layout = [
        [sg.Text('MapMyRide Data Sync & Mapping Tool', font=('Helvetica', 16))],
        [sg.Button('Quick Sync', key='-QUICK-', size=(20, 2))],
        [sg.Button('Full Sync', key='-FULL-', size=(20, 2))],
        [sg.Button('Sync from Local CSV', key='-LOCAL-', size=(20, 2))],
        [sg.Button('Generate Maps', key='-MAPS-', size=(20, 2))],
        # The Output element in v4 automatically captures stdout, so the explicit
        # redirect context manager is not needed for this implementation.
        [sg.Output(size=(80, 20), key='-OUTPUT-')],
        [sg.Button('Exit', size=(10, 1))]
    ]

    window = sg.Window('MapMyRide Control Panel', layout)

    def toggle_buttons(disabled: bool):
        """A helper function to easily disable or enable all action buttons."""
        for key in action_buttons:
            window[key].update(disabled=disabled)

    # --- 3. Event Loop ---
    while True:
        event, values = window.read()

        if event == sg.WIN_CLOSED or event == 'Exit':
            break

        if event in action_buttons:
            toggle_buttons(disabled=True)
            window.refresh()

            # CHANGE 2: Removed the 'with sg.redirect_stdout_to_swallow(...)' block.
            # The sg.Output element handles the print redirection by default in this layout.
            window['-OUTPUT-'].update('')  # Clear previous output
            try:
                if event == '-QUICK-':
                    sync_workouts(config=app_config, use_local_csv=False, full_check=False)
                elif event == '-FULL-':
                    sync_workouts(config=app_config, use_local_csv=False, full_check=True)
                elif event == '-LOCAL-':
                    sync_workouts(config=app_config, use_local_csv=True, full_check=False)
                elif event == '-MAPS-':
                    generate_maps(config=app_config)
            except Exception as e:
                print(f"\n--- AN ERROR OCCURRED ---\n")
                import traceback
                traceback.print_exc()
            finally:
                toggle_buttons(disabled=False)

    window.close()

if __name__ == "__main__":
    main()