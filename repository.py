# repository.py

import configparser
import csv
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# Import the new Workout class
from workout import Workout


def _extract_workout_id_from_filename(path: Path) -> Optional[str]:
    """
    Extracts the workout ID from a filename like '... (W12345).tcx'.
    This is a helper function specific to the repository's needs.
    """
    match = re.search(r'\(W(\d+)\)', path.name)
    if match:
        return match.group(1)
    return None


def _get_unique_filepath(directory: Path, filename: str) -> Path:
    """
    Finds a unique filepath in a directory, adding a suffix if the file exists.
    """
    filepath = directory / filename
    if not filepath.exists():
        return filepath

    stem = filepath.stem
    suffix = filepath.suffix
    counter = 1
    while filepath.exists():
        new_filename = f"{stem}_{counter:04d}{suffix}"
        filepath = directory / new_filename
        counter += 1
    return filepath


class WorkoutRepository:
    """
    Manages the persistence layer for workouts, handling all file system
    interactions for TCX files and the master CSV database.
    """

    def __init__(self, config: configparser.ConfigParser):
        """
        Initializes the repository with paths from the configuration.

        Args:
            config: The application configuration object.
        """
        self.source_folder = Path(config.get('paths', 'source_gps_track_folder'))
        self.master_csv_path = Path(config.get('paths', 'tcx_file_list'))
        self.local_csv_path = Path(config.get('debugging', 'local_csv_path'))

        # Ensure the main TCX directory exists
        self.source_folder.mkdir(parents=True, exist_ok=True)

        self.workouts: Dict[str, Workout] = {}

    def load(self):
        """
        Loads all workouts from the master CSV file into memory.
        This populates the internal `workouts` dictionary.
        """
        print(f"\n--- Loading Master Workout List from '{self.master_csv_path.name}' ---")
        if not self.master_csv_path.exists():
            print("INFO: Master CSV file not found. Starting with an empty repository.")
            return

        try:
            with open(self.master_csv_path, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    workout = Workout(row)
                    if workout.workout_id:
                        self.workouts[workout.workout_id] = workout
            print(f"Loaded {len(self.workouts)} existing workouts from the master list.")
        except Exception as e:
            print(f"ERROR: Could not read master CSV file. Reason: {e}")

    def get_all(self) -> List[Workout]:
        """Returns a list of all loaded workout objects."""
        return list(self.workouts.values())

    def get_by_id(self, workout_id: str) -> Optional[Workout]:
        """Retrieves a single workout by its ID."""
        return self.workouts.get(workout_id)

    def add_or_update(self, workout: Workout):
        """Adds a new workout or updates an existing one in the repository."""
        self.workouts[workout.workout_id] = workout

    def save_all(self):
        """
        Saves all workouts from memory back to the master CSV file.
        The list is sorted by date for consistent output.
        """
        print(f"\n--- Saving all {len(self.workouts)} workouts to '{self.master_csv_path.name}' ---")
        if not self.workouts:
            print("No workouts to save.")
            return

        # Sort workouts by date, newest first
        sorted_workouts = sorted(
            self.get_all(),
            key=lambda w: w.workout_date or datetime.min,
            reverse=True
        )

        # Get all possible fieldnames from the data to ensure no data is lost
        all_rows = [w.to_csv_row() for w in sorted_workouts]
        all_keys: Set[str] = set()
        for row in all_rows:
            all_keys.update(row.keys())

        # Define a preferred order for the main columns
        preferred_order = [
            'Date Submitted', 'Workout Date', 'Activity Type', 'Link', 'Filename',
            'Fingerprint', 'Notes', 'Distance (km)', 'Workout Time (seconds)'
        ]
        # Create a sorted list of headers with the preferred ones first
        fieldnames = sorted(
            list(all_keys),
            key=lambda x: preferred_order.index(x) if x in preferred_order else len(preferred_order)
        )

        try:
            with open(self.master_csv_path, 'w', encoding='UTF8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_rows)
            print(f"✅ Successfully wrote {len(all_rows)} records to '{self.master_csv_path.name}'.")
        except IOError as e:
            print(f"ERROR: Could not write to CSV file. Reason: {e}")

    def scan_and_build_id_map(self) -> Dict[str, Path]:
        """
        Scans the source folder for TCX files, cleans up duplicates by workout ID,
        and returns a map of workout IDs to their authoritative file paths.
        """
        print(f"\n--- Scanning for duplicates by Workout ID in '{self.source_folder.name}' ---")
        id_to_files: Dict[str, List[Path]] = {}
        for tcx_path in self.source_folder.glob("*.tcx"):
            workout_id = _extract_workout_id_from_filename(tcx_path)
            if workout_id:
                id_to_files.setdefault(workout_id, []).append(tcx_path)

        authoritative_map: Dict[str, Path] = {}
        files_deleted = 0
        for workout_id, file_list in id_to_files.items():
            if len(file_list) > 1:
                file_list.sort()
                file_to_keep = file_list[0]
                print(f"  -> Found duplicates for Workout ID {workout_id}. Keeping '{file_to_keep.name}'.")
                for file_to_delete in file_list[1:]:
                    print(f"     - Deleting duplicate file: '{file_to_delete.name}'")
                    try:
                        file_to_delete.unlink()
                        files_deleted += 1
                    except OSError as e:
                        print(f"     - ERROR: Could not delete file: {e}")
                authoritative_map[workout_id] = file_to_keep
            else:
                authoritative_map[workout_id] = file_list[0]

        if files_deleted > 0:
            print(f"Cleanup complete. Deleted {files_deleted} duplicate files.")
        else:
            print("No duplicates found based on workout ID.")

        return authoritative_map

    def save_tcx_file(self, temp_path: Path, workout: Workout) -> Optional[Path]:
        """
        Moves a temporary TCX file to its final, standardized location.

        Args:
            temp_path: The path to the downloaded file in the temporary directory.
            workout: The Workout object this file belongs to.

        Returns:
            The final path of the saved file, or None if the move fails.
        """
        new_filename_stem = workout.generate_filename_stem()
        final_tcx_path = _get_unique_filepath(self.source_folder, f"{new_filename_stem}.tcx")

        try:
            shutil.move(temp_path, final_tcx_path)
            print(f"  - 💾 SAVED: Renamed and moved to '{final_tcx_path.name}'")
            return final_tcx_path
        except (OSError, shutil.Error) as e:
            print(f"  - ❌ FAILED: Could not move file. Reason: {e}")
            return None
