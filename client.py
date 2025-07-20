# client.py

import configparser
import os
import shutil
import time
from pathlib import Path
from typing import Optional, Set

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def get_downloads_folder() -> Path:
    """
    Returns the path to the user's Downloads folder as a Path object.
    This is a standard location for temporary file downloads.
    """
    return Path.home() / "Downloads"


class MapMyRideClient:
    """
    A client to handle all web interactions with MapMyRide.

    This class uses "just-in-time" login. The browser is only launched
    and login is only performed when a download method is first called.
    It should be used as a context manager to ensure cleanup.
    """

    def __init__(self, config: configparser.ConfigParser):
        """
        Initializes the client with application configuration.

        Args:
            config: The application's configuration object.
        """
        self._config = config
        self.browser: Optional[webdriver.Chrome] = None
        self.temp_download_dir: Optional[Path] = None
        self._is_logged_in = False

    def __enter__(self) -> "MapMyRideClient":
        """Enters the context, making the client ready for use but NOT logging in yet."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Cleans up resources by closing the browser and deleting temporary files.
        This is guaranteed to run when the 'with' block is exited.
        """
        print("\n--- Shutting Down Web Client (if active) ---")
        if self.browser:
            self.browser.quit()
            print("Browser closed.")
        if self.temp_download_dir and self.temp_download_dir.exists():
            shutil.rmtree(self.temp_download_dir)
            print(f"Cleaned up temporary directory: {self.temp_download_dir}")

    def _ensure_login_and_browser(self) -> bool:
        """
        Ensures the browser is running and the user is logged in.
        This is the core of the "just-in-time" login logic. It only
        runs once when the first download is requested.
        """
        if self._is_logged_in:
            return True

        print("\n--- JIT: Initializing Web Client and Logging In ---")
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
            print(f"FATAL: Failed to initialize browser and log in. Reason: {e}")
            self.__exit__(None, None, None)
            return False

    def _login(self) -> bool:
        if not self.browser:
            return False

        login_url = self._config.get('urls', 'login_url')
        username = os.environ.get('MAPMYRIDE_USERNAME')
        password = os.environ.get('MAPMYRIDE_PASSWORD')

        if not username or not password:
            print("FATAL: Environment variables MAPMYRIDE_USERNAME or MAPMYRIDE_PASSWORD not set.")
            print("Please ensure they are set correctly in your system environment.")
            return False

        password_input_id = self._config.get('selectors', 'password_input_id')
        email_input_id = self._config.get('selectors', 'email_input_id')

        print("Navigating to login page...")
        self.browser.get(login_url)
        try:
            print("Waiting up to 120 seconds for login page to be ready (for CAPTCHA)...")
            WebDriverWait(self.browser, 120).until(EC.presence_of_element_located((By.ID, password_input_id)))
            print("Page is ready! Entering credentials...")

            email_el = self.browser.find_element(by=By.ID, value=email_input_id)
            email_el.send_keys(username)

            pwd_el = self.browser.find_element(by=By.ID, value=password_input_id)
            pwd_el.send_keys(password)

            login_button_xpath = "//button[contains(., 'Log In')]"
            login_button = self.browser.find_element(by=By.XPATH, value=login_button_xpath)
            login_button.click()

            WebDriverWait(self.browser, 120).until(EC.url_changes(login_url))
            print("Login successful.")
            return True

        except TimeoutException:
            print("ERROR: Loading login page or finding elements took too much time!")
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

        print(f"Download timed out. Failed to download file from URL: {url}")
        return None

    def download_workout_list_csv(self) -> Optional[Path]:
        """Downloads the main CSV of all workouts, ensuring login first."""
        if not self._ensure_login_and_browser():
            return None

        csv_export_url = self._config.get('urls', 'csv_export_url')
        print(f"\nDownloading workout list CSV from {csv_export_url}...")
        return self._download_file_and_wait(csv_export_url)

    def download_tcx_file(self, workout_id: str) -> Optional[Path]:
        """Downloads a single TCX file for a given workout ID, ensuring login first."""
        if not self._ensure_login_and_browser():
            return None

        tcx_export_url_template = self._config.get('urls', 'tcx_export_url_template')
        download_url = tcx_export_url_template.format(workout_id=workout_id)
        print(f"  - ⬇️ DOWNLOADING: No existing file found for ID {workout_id}.")
        return self._download_file_and_wait(download_url)
