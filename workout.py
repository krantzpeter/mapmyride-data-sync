# C:/Users/krant/PycharmProjects/SelMapExtract/workout.py

from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import lxml.etree as ET
import os
import logging

log = logging.getLogger(__name__)

# List of keywords that commonly appear in default MapMyRide titles
# We strip these to avoid "double-ups" in filenames.
ACTIVITY_KEYWORDS = [
    'Road Cycling', 'Bike Ride', 'Cycling', 'Hike', 'Walk',
    'Running', 'Run', 'Mountain Biking', 'Walk/Hike'
]


def _get_workout_id_from_link(link: str) -> str:
    if not link:
        return ''
    return link.strip('/').split('/')[-1]


def _parse_csv_date_str(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    cleaned_date_str = date_str.replace('Sept.', 'Sep.').replace('.', '')
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(cleaned_date_str, fmt)
        except ValueError:
            continue
    return None


def _create_fingerprint(total_time_seconds: str, distance_m: str) -> str:
    try:
        time_sec = int(round(float(total_time_seconds or 0)))
    except (ValueError, TypeError):
        time_sec = 0
    try:
        dist_cm = int(round(float(distance_m or 0) * 100))
    except (ValueError, TypeError):
        dist_cm = 0
    return f"T{time_sec:08d}D{dist_cm:010d}"


def _extract_tcx_file_properties(tcx_filename: str) -> Dict[str, str]:
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
    except Exception as e:
        log.error(f"Error parsing TCX '{os.path.basename(tcx_filename)}': {e}")
        return {}
    return prop_dict


class Workout:
    """
    Represents a single workout.
    Maintains a clean CSV schema by keeping 'Proper Names' in memory only.
    """

    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self.tcx_path: Optional[Path] = Path(data.get('Filename')) if data.get('Filename') else None

        # temp_proper_name is used to hold names recovered from the disk or scraper.
        # This is never saved to the CSV.
        self.temp_proper_name: Optional[str] = None

    @property
    def workout_id(self) -> str:
        return _get_workout_id_from_link(self._data.get('Link', ''))

    @property
    def workout_date(self) -> Optional[datetime]:
        return _parse_csv_date_str(self._data.get('Workout Date', ''))

    @property
    def activity_type(self) -> str:
        return self._data.get('Activity Type', 'Unknown Activity')

    @property
    def distance_km(self) -> float:
        try:
            return float(self._data.get('Distance (km)', 0.0))
        except (ValueError, TypeError):
            return 0.0

    @property
    def workout_name(self) -> str:
        """
        Returns the most descriptive name available.
        Prioritizes the runtime-recovered name over the CSV 'Notes' field.
        """
        if self.temp_proper_name:
            return self.temp_proper_name

        # We also check the raw dictionary for 'Workout Name' in case it was
        # populated by a previous scrape in the same session.
        return self._data.get('Workout Name', self.notes)

    @property
    def notes(self) -> str:
        return self._data.get('Notes', '')

    @property
    def stored_fingerprint(self) -> Optional[str]:
        return self._data.get('Fingerprint')

    @property
    def fingerprint(self) -> Optional[str]:
        if self.tcx_path and self.tcx_path.exists():
            props = _extract_tcx_file_properties(str(self.tcx_path))
            return _create_fingerprint(props.get('TotalTimeSeconds', '0'), props.get('DistanceMeters', '0'))
        return self.stored_fingerprint

    def update_fingerprint(self, new_fingerprint: str):
        self._data['Fingerprint'] = new_fingerprint

    def update_from_online_data(self, new_data: Dict[str, Any]):
        self._data.update(new_data)


    def generate_filename_stem(self) -> str:
        """
            Generates the standardized base name for the TCX file.
            Format: yyyy mm dd <title> <distance>km <Activity> (W<workout no>)
            """
        date_prefix = self.workout_date.strftime('%Y %m %d') if self.workout_date else "0000 00 00"
        activity_display = self.activity_type.replace('/', '_')

        # Prioritize the cleaned proper name
        raw_name = self.workout_name
        cleaned_name = raw_name

        if cleaned_name:
            # 1. Strip redundant distance patterns anywhere (e.g., "11.62km" or "11.6 km")
            dist_patterns = [
                rf'{self.distance_km:.2f}\s*km',
                rf'{self.distance_km:g}\s*km'
            ]
            for dp in dist_patterns:
                cleaned_name = re.sub(dp, '', cleaned_name, flags=re.IGNORECASE)

            # 2. Strip synonyms and activity keywords anywhere
            # CRITICAL: We sort by length descending to ensure "Road Cycling"
            # is stripped before "Cycling", otherwise "Road" gets left behind.
            all_synonyms = sorted(
                list(set(ACTIVITY_KEYWORDS + [self.activity_type])),
                key=len,
                reverse=True
            )

            for keyword in all_synonyms:
                # Use word boundaries (\b) to ensure we don't strip "Walk" out of "Skywalk"
                cleaned_name = re.sub(rf'\b{re.escape(keyword)}\b', '', cleaned_name, flags=re.IGNORECASE)

            # 3. Strip Windows-illegal characters
            cleaned_name = re.sub(r'[<>:"/\\|?*]', '', cleaned_name)

            # 4. Final Cleanup: Collapse multiple spaces and strip ends
            cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()

        # If after scrubbing, the title is empty (meaning it was just metadata),
        # name_part will be empty and we avoid the double spaces.
        name_part = f" {cleaned_name}" if cleaned_name else ""

        return f"{date_prefix}{name_part} {self.distance_km:.2f}km {activity_display} (W{self.workout_id})"

    def to_csv_row(self) -> Dict[str, Any]:
        """
        Prepares the row for persistence.
        Explicitly removes the 'Workout Name' column to maintain the desired CSV schema.
        """
        row_copy = self._data.copy()
        row_copy['Filename'] = str(self.tcx_path) if self.tcx_path else ''

        # Ensure we return only original columns, excluding our memory-only 'Workout Name'
        return {k: v for k, v in row_copy.items() if k != 'Workout Name'}

    def __repr__(self) -> str:
        return f"<Workout ID={self.workout_id} Name='{self.workout_name}'>"
