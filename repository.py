# C:/Users/krant/PycharmProjects/SelMapExtract/repository.py

import configparser
import csv
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

from workout import Workout


def _extract_metadata_from_filename(path: Path) -> Dict[str, Any]:
    """
    Parses a descriptive filename to extract ID and Title.
    Pattern: YYYY MM DD [Title] [Distance]km [Activity] (W[ID]).tcx
    """
    metadata = {'id': None, 'title': None}

    # 1. Extract Workout ID from the (W[ID]) suffix
    id_match = re.search(r'\(W(\d+)\)', path.name)
    if not id_match:
        return metadata
    metadata['id'] = id_match.group(1)

    # 2. Extract Title (The text between the date and the distance/activity)
    # Regex looks for text after the YYYY MM DD prefix and before the numeric distance
    stem = path.stem
    title_match = re.search(r'^\d{4} \d{2} \d{2}\s+(.*?)\s+\d+\.\d+km', stem)
    if title_match:
        metadata['title'] = title_match.group(1).strip()

    return metadata


def _get_unique_filepath(directory: Path, filename: str) -> Path:
    filepath = directory / filename
    if not filepath.exists():
        return filepath
    stem = filepath.stem
    suffix = filepath.suffix
    counter = 1
    while filepath.exists():
        filepath = directory / f"{stem}_{counter:04d}{suffix}"
        counter += 1
    return filepath


class WorkoutRepository:
    def __init__(self, config: configparser.ConfigParser):
        self.source_folder = Path(config.get('paths', 'source_gps_track_folder'))
        self.master_csv_path = Path(config.get('paths', 'tcx_file_list'))
        self.local_csv_path = Path(config.get('debugging', 'local_csv_path'))
        self.source_folder.mkdir(parents=True, exist_ok=True)
        self.workouts: Dict[str, Workout] = {}

    def load(self):
        print(f"\n--- Loading Master Workout List from '{self.master_csv_path.name}' ---")
        if not self.master_csv_path.exists():
            return
        with open(self.master_csv_path, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                workout = Workout(row)
                if workout.workout_id:
                    self.workouts[workout.workout_id] = workout
        print(f"Loaded {len(self.workouts)} existing workouts from the master list.")

    def get_all(self) -> List[Workout]:
        return list(self.workouts.values())

    def get_by_id(self, workout_id: str) -> Optional[Workout]:
        return self.workouts.get(workout_id)

    def add_or_update(self, workout: Workout):
        self.workouts[workout.workout_id] = workout

    def save_all(self):
        print(f"\n--- Saving all {len(self.workouts)} workouts to '{self.master_csv_path.name}' ---")
        if not self.workouts:
            return
        sorted_workouts = sorted(self.get_all(), key=lambda w: w.workout_date or datetime.min, reverse=True)
        all_rows = [w.to_csv_row() for w in sorted_workouts]

        all_keys: Set[str] = set()
        for row in all_rows:
            all_keys.update(row.keys())

        preferred_order = ['Date Submitted', 'Workout Date', 'Activity Type', 'Link', 'Filename', 'Fingerprint',
                           'Notes', 'Distance (km)', 'Workout Time (seconds)']
        fieldnames = sorted(list(all_keys),
                            key=lambda x: preferred_order.index(x) if x in preferred_order else len(preferred_order))

        with open(self.master_csv_path, 'w', encoding='UTF8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"✅ Successfully wrote {len(all_rows)} records to '{self.master_csv_path.name}'.")

    def scan_and_build_id_map(self) -> Dict[str, Dict[str, Any]]:
        """
        Scans for TCX files and cleans up duplicates.
        Returns a map of ID -> {path: Path, title: str}
        """
        print(f"\n--- Scanning folder and recovering titles from '{self.source_folder.name}' ---")
        id_to_files: Dict[str, List[Path]] = {}
        for tcx_path in self.source_folder.glob("*.tcx"):
            meta = _extract_metadata_from_filename(tcx_path)
            if meta['id']:
                id_to_files.setdefault(meta['id'], []).append(tcx_path)

        authoritative_map: Dict[str, Dict[str, Any]] = {}
        for workout_id, file_list in id_to_files.items():
            file_list.sort()
            file_to_keep = file_list[0]
            if len(file_list) > 1:
                for file_to_delete in file_list[1:]:
                    try:
                        file_to_delete.unlink()
                    except OSError:
                        pass

            # Extract final metadata for the kept file
            meta = _extract_metadata_from_filename(file_to_keep)
            authoritative_map[workout_id] = {'path': file_to_keep, 'title': meta['title']}

        return authoritative_map


def save_tcx_file(self, temp_path: Path, workout: Workout, ignore_if_exists: bool = False) -> Optional[Path]:
    """
        Moves/Renames a TCX file to the repository using standardized naming.

        Args:
            temp_path: The current location of the file.
            workout: The workout object used to generate the new name.
            ignore_if_exists: If True, returns the path immediately if the file is
                              already at the destination with the correct name.
                              Defaults to False to preserve original behavior.
        """
    new_filename_stem = workout.generate_filename_stem()
    new_filename = f"{new_filename_stem}.tcx"
    target_path = self.source_folder / new_filename

    # OPTIONAL IDEMPOTENCY CHECK:
    # Only triggers if explicitly requested by the caller (e.g., during repair/rename).
    if ignore_if_exists:
        if temp_path.exists() and temp_path.resolve() == target_path.resolve():
            log.info(f"  - Filename is already correct: {new_filename}")
            return target_path

    # Standard behavior: ensure a unique name (appends _0001 if a DIFFERENT file exists)
    final_tcx_path = _get_unique_filepath(self.source_folder, new_filename)
    try:
        shutil.move(temp_path, final_tcx_path)
        print(f"  - 💾 SAVED: Renamed and moved to '{final_tcx_path.name}'")
        return final_tcx_path
    except (OSError, shutil.Error) as e:
        print(f"  - ❌ FAILED: Could not move file: {e}")
        return None
