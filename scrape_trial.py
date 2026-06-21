# C:/Users/krant/PycharmProjects/SelMapExtract/scrape_trial.py

import configparser
import logging
import time
from pathlib import Path
from client import MapMyRideClient

# Setup logging to match project style
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


def run_scrape_trial(workout_id: str):
    """
    Standalone trial to verify the 'Proper Name' scraping logic.
    Uses the MapMyRideClient to bypass Cloudflare via the Remote Debugging
    port verified in the provided HTML.
    """
    # 1. Load Configuration
    config = configparser.ConfigParser()
    config_path = 'config.ini'

    if not Path(config_path).exists():
        log.error(f"FATAL: Configuration file not found at {config_path}")
        return

    config.read(config_path)

    log.info(f"--- STARTING SCRAPE TRIAL FOR ID: {workout_id} ---")

    # 2. Initialize the Client
    # This will either attach to your open Chrome or launch one and wait for you to login.
    client = MapMyRideClient(config)

    try:
        start_time = time.time()

        # 3. Use the production method to test scraping
        # This method uses the 'Metadata Theft' (og:title) logic
        workout_name = client.fetch_workout_name(workout_id)

        end_time = time.time()

        if workout_name:
            log.info("--- TRIAL RESULTS ---")
            log.info(f"Target ID:      {workout_id}")
            log.info(f"Extracted Name: {workout_name}")
            log.info(f"Time Taken:     {end_time - start_time:.2f} seconds")
            log.info("----------------------")
        else:
            log.error(f"Trial failed: Could not recover name for ID {workout_id}")
            log.info("TIP: Ensure you are logged in and the workout is visible in the browser.")

    except Exception as e:
        log.error(f"Trial crashed: {e}")
    finally:
        # Releasing control without closing the browser
        client.__exit__(None, None, None)
        log.info("Trial complete. Browser remains open for next run.")


if __name__ == "__main__":
    # Test workout ID (Example)
    run_scrape_trial("8649434867")
