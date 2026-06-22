# C:/Users/krant/PycharmProjects/SelMapExtract/integration_test.py

import configparser
import logging
import sys
from pathlib import Path
from client import MapMyRideClient
from repository import WorkoutRepository
from map_generator import MapGenerator
from workout import Workout

# Setup local logging for the test
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
log = logging.getLogger("IntegrationTest")


def run_system_check():
    """
    Performs an end-to-end integration test of the SelMapExtract pipeline.
    """
    log.info("🚀 Starting System Integration Check...")

    # 1. Configuration Check
    config = configparser.ConfigParser()
    config_path = Path('config.ini')
    if not config_path.exists():
        log.error("❌ Step 1 Failed: config.ini not found.")
        return
    config.read(config_path)
    log.info("✅ Step 1: Configuration loaded.")

    # 2. Repository Check
    try:
        repo = WorkoutRepository(config)
        repo.load()
        all_workouts = repo.get_all()
        if not all_workouts:
            log.warning("⚠️ Step 2 Warning: Repository loaded but no workouts found.")
        else:
            log.info(f"✅ Step 2: Repository loaded {len(all_workouts)} workouts.")
    except Exception as e:
        log.error(f"❌ Step 2 Failed: Repository error: {e}")
        return

    # 3. Client & Connectivity Check (The most fragile part)
    client = MapMyRideClient(config)
    try:
        log.info("Checking browser connection (Remote Debugging)...")
        if client._ensure_login_and_browser():
            log.info("✅ Step 3: Successfully attached to Chrome and bypassed security.")
        else:
            log.error("❌ Step 3 Failed: Could not connect to Chrome.")
            return
    except Exception as e:
        log.error(f"❌ Step 3 Failed: Browser attachment error: {e}")
        return

    # 4. Scraping Check (Metadata Theft)
    # Testing with a known workout ID from your earlier logs
    test_id = "8649434867"
    try:
        name = client.fetch_workout_name(test_id)
        if name:
            log.info(f"✅ Step 4: Successfully scraped test workout name: '{name}'")
        else:
            log.error("❌ Step 4 Failed: Scraper returned empty name.")
    except Exception as e:
        log.error(f"❌ Step 4 Failed: Scraping logic error: {e}")

    # 5. Map Generation Check
    try:
        map_gen = MapGenerator(config)
        # Just check if we can initialize and find the folder
        if map_gen.simplified_folder.exists():
            log.info(f"✅ Step 5: MapGenerator initialized. Target: {map_gen.map_file_path}")
        else:
            log.error("❌ Step 5 Failed: Simplified folder path invalid.")
    except Exception as e:
        log.error(f"❌ Step 5 Failed: MapGenerator error: {e}")

    log.info("--- INTEGRATION CHECK COMPLETE ---")
    client.__exit__(None, None, None)


if __name__ == "__main__":
    run_system_check()
