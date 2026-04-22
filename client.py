# client.py

import configparser
import os
import shutil
import time
import logging  # Added for unified logging
from pathlib import Path
from typing import Optional, Set

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Initialize logger to match the pattern in workout.py
log = logging.getLogger(__name__)


def get_downloads_folder() -> Path:
    """
    Returns the path to the user's Downloads folder as a Path object.
    """
    return Path.home() / "Downloads"


class MapMyRideClient:
    """
    A client to handle all web interactions with MapMyRide.
    """

    def __init__(self, config: configparser.ConfigParser):
        self._config = config
        self.browser: Optional[webdriver.Chrome] = None
        self.temp_download_dir: Optional[Path] = None
        self._is_logged_in = False

    def __enter__(self) -> "MapMyRideClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        log.info("--- Shutting Down Web Client (if active) ---")
        if self.browser:
            self.browser.quit()
            log.info("Browser closed.")
        if self.temp_download_dir and self.temp_download_dir.exists():
            shutil.rmtree(self.temp_download_dir)
            log.info(f"Cleaned up temporary directory: {self.temp_download_dir}")

    def fetch_workout_name(self, workout_id: str) -> str:
        """
        Navigates to the workout page and extracts the 'Proper Name' from metadata.
        Uses the optimized 'Metadata Theft' strategy to bypass map rendering.
        """
        if not self._ensure_login_and_browser():
            return ""

        target_url = f"https://www.mapmyride.com/workout/{workout_id}"
        log.info(f"  - Scraping title for ID {workout_id}...")

        try:
            # 1. Enable network blocking to stop the 'slow and painful' map render
            self.browser.execute_cdp_cmd("Network.enable", {})
            self.browser.execute_cdp_cmd("Network.setBlockedURLs", {
                "urls": ["*maplibregl*", "*mapmy-static*", "*maps.googleapis.com*"]
            })

            # 2. Navigate to the page
            self.browser.get(target_url)

            # 3. Extract the metadata (og:title)
            wait = WebDriverWait(self.browser, 10)
            meta_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//meta[@property='og:title']")
            ))

            name = meta_element.get_attribute("content")

            # Clean the name by removing the site suffix if present
            if name and " | MapMyRide" in name:
                name = name.split(" | MapMyRide")[0].strip()

            # Fallback if metadata is generic or just the site name
            if not name or name.lower() == "mapmyride":
                title_xpath = "/html/body/div[2]/div/div[3]/div/div/div[1]/div[1]/div/div/div[2]/div[1]/div[1]/div/h4"
                title_el = self.browser.find_element(By.XPATH, title_xpath)
                name = title_el.text.strip()

            # New status message to show the extracted title
            if name:
                log.info(f"    - SUCCESS: Extracted name: {name}")
            else:
                log.warning(f"    - WARNING: No name found for workout ID {workout_id}")

            return name.strip()

        except Exception as e:
            log.error(f"  - Failed to scrape name for {workout_id}: {e}")
            return ""
        finally:
            if self.browser:
                self.browser.execute_cdp_cmd("Network.setBlockedURLs", {"urls": []})

    def _ensure_login_and_browser(self) -> bool:
        if self._is_logged_in:
            return True

        log.info("--- JIT: Initializing Web Client and Logging In ---")
        try:
            self.temp_download_dir = get_downloads_folder() / f"sel_map_extract_temp_{int(time.time())}"
            self.temp_download_dir.mkdir(parents=True, exist_ok=True)

            chrome_options = ChromeOptions()
            prefs = {"download.default_directory": str(self.temp_download_dir)}
            chrome_options.add_experimental_option("prefs", prefs)

            self.browser = webdriver.Chrome(options=chrome_options)

            if not self._login():
                raise ConnectionError("FATAL: Login to MapMyRide failed. Aborting.")

            self._is_logged_in = True
            return True
        except Exception as e:
            log.error(f"FATAL: Failed to initialize browser and log in. Reason: {e}")
            self.__exit__(None, None, None)
            return False

    def _login(self) -> bool:
        if not self.browser:
            return False

        login_url = self._config.get('urls', 'login_url')
        username = os.environ.get('MAPMYRIDE_USERNAME')
        password = os.environ.get('MAPMYRIDE_PASSWORD')

        if not username or not password:
            log.error("FATAL: Environment variables MAPMYRIDE_USERNAME or MAPMYRIDE_PASSWORD not set.")
            return False

        password_input_id = self._config.get('selectors', 'password_input_id')
        email_input_id = self._config.get('selectors', 'email_input_id')

        log.info("Navigating to login page...")
        self.browser.get(login_url)
        try:
            log.info("Waiting up to 120 seconds for login page to be ready (for CAPTCHA)...")
            WebDriverWait(self.browser, 120).until(EC.presence_of_element_located((By.ID, password_input_id)))
            log.info("Page is ready! Entering credentials...")

            email_el = self.browser.find_element(by=By.ID, value=email_input_id)
            email_el.send_keys(username)

            pwd_el = self.browser.find_element(by=By.ID, value=password_input_id)
            pwd_el.send_keys(password)

            login_button_xpath = "//button[contains(., 'Log In')]"
            login_button = self.browser.find_element(by=By.XPATH, value=login_button_xpath)
            login_button.click()

            WebDriverWait(self.browser, 120).until(EC.url_changes(login_url))
            log.info("Login successful.")
            return True

        except TimeoutException:
            log.error("ERROR: Loading login page or finding elements took too much time!")
            return False

    def _download_file_and_wait(self, url: str) -> Optional[Path]:
        if not self.browser or not self.temp_download_dir:
            return None

        files_before: Set[str] = {p.name for p in self.temp_download_dir.iterdir()}
        self.browser.get(url)

        for _ in range(60):
            time.sleep(0.5)
            files_after: Set[str] = {p.name for p in self.temp_download_dir.iterdir()}
            new_files: Set[str] = files_after - files_before

            if new_files:
                new_file_name = new_files.pop()
                if not new_file_name.endswith(('.tmp', '.crdownload')):
                    return self.temp_download_dir / new_file_name

        log.error(f"Download timed out. Failed to download file from URL: {url}")
        return None

    def download_workout_list_csv(self) -> Optional[Path]:
        if not self._ensure_login_and_browser():
            return None

        csv_export_url = self._config.get('urls', 'csv_export_url')
        log.info(f"Downloading workout list CSV from {csv_export_url}...")
        return self._download_file_and_wait(csv_export_url)

    def download_tcx_file(self, workout_id: str) -> Optional[Path]:
        if not self._ensure_login_and_browser():
            return None

        tcx_export_url_template = self._config.get('urls', 'tcx_export_url_template')
        download_url = tcx_export_url_template.format(workout_id=workout_id)
        log.info(f"  - ⬇️ DOWNLOADING: No existing file found for ID {workout_id}.")
        return self._download_file_and_wait(download_url)
