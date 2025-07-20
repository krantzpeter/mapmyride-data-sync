# workout.py

from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import lxml.etree as ET
import os


# --- Helper functions that are tightly coupled to workout data ---

def _get_workout_id_from_link(link: str) -> str:
    """Extracts the unique workout ID from a MapMyRide workout URL."""
    if not link:
        return ''
    return link.strip('/').split('/')[-1]


def _parse_csv_date_str(date_str: str) -> Optional[datetime]:
    """Robustly parses a date string from the MapMyRide CSV into a datetime object."""
    if not date_str:
        return None
    cleaned_date_str = date_str.replace('Sept.', 'Sep.').replace('.', '')
    for fmt in ('%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(cleaned_date_str, fmt)
        except ValueError:
            continue
    return None


def _create_fingerprint(total_time_seconds: str, distance_m: str) -> str:
    """Creates a unique, robust fingerprint based on workout duration and distance."""
    try:
        time_sec = int(round(float(total_time_seconds or 0)))
    except (ValueError, TypeError):
        time_sec = 0
    try:
        dist_cm = int(round(float(distance_m or 0) * 100))
    except (ValueError, TypeError):
        dist_cm = 0
    return f"T{time_sec:08d}D{dist_cm:010d}"


# This function is temporarily needed here for the Workout class to function independently.
def _extract_tcx_file_properties(tcx_filename: str) -> Dict[str, str]:
    """Reads a TCX file and extracts specified properties into a Dictionary."""
    prop_dict = {}
    try:
        tree = ET.parse(tcx_filename)
        root = tree.getroot()
        ns_uri = root.nsmap.get(None, '')
        ns = f'{{{ns_uri}}}' if ns_uri else ''
        activity_el = root.find(f'.//{ns}Activity')
        if activity_el is not None:
            prop_dict['Activity'] = activity_el.get('Sport')
        attribs = ('TotalTimeSeconds', 'DistanceMeters')
        for attrib in attribs:
            el = root.find(f'.//{ns}{attrib}')
            if el is not None:
                prop_dict[attrib] = el.text
    except ET.ParseError as e:
        print(f"ERROR: Could not parse TCX file '{os.path.basename(tcx_filename)}'. Reason: {e}")
        return {}
    return prop_dict


# --- The main Workout class ---

class Workout:
    """
    Represents a single workout, encapsulating its data and related logic.
    This class is the central data model for the application.
    """

    def __init__(self, data: Dict[str, Any]):
        """
        Initializes a Workout object from a dictionary of data, typically a CSV row.

        Args:
            data: A dictionary containing the workout's properties.
        """
        self._data = data
        self.tcx_path: Optional[Path] = Path(data.get('Filename')) if data.get('Filename') else None

    @property
    def workout_id(self) -> str:
        """The unique ID of the workout, extracted from the link."""
        return _get_workout_id_from_link(self._data.get('Link', ''))

    @property
    def workout_date(self) -> Optional[datetime]:
        """The date the workout was performed, as a datetime object."""
        return _parse_csv_date_str(self._data.get('Workout Date', ''))

    @property
    def activity_type(self) -> str:
        """The type of activity (e.g., 'Walk', 'Bike Ride')."""
        return self._data.get('Activity Type', 'Unknown Activity')

    @property
    def distance_km(self) -> float:
        """The distance of the workout in kilometers."""
        try:
            return float(self._data.get('Distance (km)', 0.0))
        except (ValueError, TypeError):
            return 0.0

    @property
    def notes(self) -> str:
        """User-provided notes for the workout."""
        return self._data.get('Notes', '')

    @property
    def stored_fingerprint(self) -> Optional[str]:
        """The fingerprint value as it was originally loaded from the CSV."""
        return self._data.get('Fingerprint')

    @property
    def fingerprint(self) -> Optional[str]:
        """
        The authoritative fingerprint of the workout, calculated from its TCX file.
        Returns None if the TCX file doesn't exist or can't be processed.
        """
        if self.tcx_path and self.tcx_path.exists():
            try:
                props = _extract_tcx_file_properties(str(self.tcx_path))
                time_sec = props.get('TotalTimeSeconds', '0')
                dist_m = props.get('DistanceMeters', '0')
                return _create_fingerprint(time_sec, dist_m)
            except Exception as e:
                print(f"Could not get fingerprint for {self.tcx_path.name}: {e}")
                return None
        # Fallback to CSV data if no file exists, for comparison purposes
        return self._data.get('Fingerprint')

        # In workout.py, inside the Workout class

    def update_fingerprint(self, new_fingerprint: str):
        """Explicitly sets the fingerprint value in the internal data dictionary."""
        self._data['Fingerprint'] = new_fingerprint

    def update_from_online_data(self, new_data: Dict[str, Any]):
        """
        Updates the workout's internal data with new data from an online source.
        This is the designated way to merge new CSV data into an existing object.

        Args:
            new_data: A dictionary of new data, typically from a fresh CSV row.
        """
        self._data.update(new_data)

    def generate_filename_stem(self) -> str:
        """Generates the standardized base name for the TCX file, without the extension."""
        date_prefix = self.workout_date.strftime('%Y %m %d') if self.workout_date else "0000 00 00"

        # Sanitize activity type for filename
        activity_prefix = self.activity_type.replace('/', '_')

        # Sanitize notes for filename
        cleaned_notes = re.sub(r'[<>:"/\\|?*]', '', self.notes).strip()
        # Truncate long notes to keep filenames manageable
        notes_suffix = (cleaned_notes[:50] + '..') if len(cleaned_notes) > 50 else cleaned_notes

        return (f"{date_prefix} {notes_suffix} {self.distance_km:.2f}km "
                f"{activity_prefix} (W{self.workout_id})")

        # workout.py -> Workout.to_csv_row() (Updated)

    def to_csv_row(self) -> Dict[str, Any]:
        """
        Converts the Workout object back into a dictionary suitable for CSV writing.
        This method is now very fast as it only reflects the current data in memory
        without triggering new file I/O.
        """
        # Ensure the filename is up-to-date in the data dictionary
        self._data['Filename'] = str(self.tcx_path) if self.tcx_path else ''
        # The fingerprint is now updated explicitly by the sync logic, not here.
        return self._data

    def __repr__(self) -> str:
        """A developer-friendly representation of the Workout object."""
        date_str = self.workout_date.strftime('%Y-%m-%d') if self.workout_date else 'No Date'
        return f"<Workout ID={self.workout_id} Date='{date_str}' Activity='{self.activity_type}'>"
