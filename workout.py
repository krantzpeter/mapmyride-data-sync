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
    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self.tcx_path: Optional[Path] = Path(data.get('Filename')) if data.get('Filename') else None
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
    def duration_sec(self) -> float:
        try:
            return float(self._data.get('Workout Time (seconds)', 0.0))
        except (ValueError, TypeError):
            return 0.0

    @property
    def is_empty(self) -> bool:
        """Returns True if the workout has no distance and no time."""
        return self.distance_km <= 0 and self.duration_sec <= 0

    @property
    def workout_name(self) -> str:
        if self.temp_proper_name:
            return self.temp_proper_name
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
        date_prefix = self.workout_date.strftime('%Y %m %d') if self.workout_date else "0000 00 00"
        activity_display = self.activity_type.replace('/', '_')
        raw_name = self.workout_name
        cleaned_name = raw_name
        if cleaned_name:
            dist_patterns = [rf'{self.distance_km:.2f}\s*km', rf'{self.distance_km:g}\s*km']
            for dp in dist_patterns:
                cleaned_name = re.sub(dp, '', cleaned_name, flags=re.IGNORECASE)
            all_synonyms = sorted(list(set(ACTIVITY_KEYWORDS + [self.activity_type])), key=len, reverse=True)
            for keyword in all_synonyms:
                cleaned_name = re.sub(rf'\b{re.escape(keyword)}\b', '', cleaned_name, flags=re.IGNORECASE)
            cleaned_name = re.sub(r'[<>:"/\\|?*]', '', cleaned_name)
            cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()

        name_part = f" {cleaned_name}" if cleaned_name else ""
        return f"{date_prefix}{name_part} {self.distance_km:.2f}km {activity_display} (W{self.workout_id})"

    def to_csv_row(self) -> Dict[str, Any]:
        row_copy = self._data.copy()
        row_copy['Filename'] = str(self.tcx_path) if self.tcx_path else ''
        return {k: v for k, v in row_copy.items() if k != 'Workout Name'}

    def __repr__(self) -> str:
        return f"<Workout ID={self.workout_id} Name='{self.workout_name}'>"
