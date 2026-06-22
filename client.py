# C:/Users/krant/PycharmProjects/SelMapExtract/client.py

import configparser
import os
import time
import socket
import subprocess
import logging
from pathlib import Path
from typing import Optional, Set

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
# noinspection PyPep8Naming
from selenium.webdriver.common.by import By
# noinspection PyPep8Naming
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Initialize logger
log = logging.getLogger(__name__)


def get_downloads_folder() -> Path:
    """Returns the path to the user's Downloads folder as a Path object."""
    return Path.home() / "Downloads"


class MapMyRideClient:
    """
    A client to handle web interactions with MapMyRide by attaching to a
    persistent Chrome instance with stealth scripts to bypass anti-bot measures.
    """

    def __init__(self, config: configparser.ConfigParser):
        self._config = config
        self.browser: Optional[webdriver.Chrome] = None
        self.temp_download_dir: Optional[Path] = None
        self._is_logged_in = False

        self.port = config.getint('selenium', 'remote_debugging_port', fallback=9222)
        self.chrome_path = config.get('selenium', 'chrome_path')
        self.user_data_dir = Path(config.get('selenium', 'user_data_dir'))

    def __enter__(self) -> "MapMyRideClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        log.info("--- Releasing Web Client ---")
        self.browser = None
        log.info("Browser control detached (instance remains open).")

    def _is_chrome_running(self) -> bool:
        """Checks if the remote debugging port is open."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', self.port)) == 0

    def _ensure_login_and_browser(self) -> bool:
        """Prioritizes connecting to an existing Chrome instance."""
        if self._is_logged_in and self.browser:
            try:
                # Simple heartbeat check to see if the session is alive
                _ = self.browser.current_url
                return True
            except Exception:
                log.warning("Existing browser control lost. Attempting to re-attach...")
                self.browser = None

        log.info("--- Initializing Chrome Connection ---")

        if not self._is_chrome_running():
            log.info(f"Port {self.port} not active. Launching a new Chrome instance...")
            try:
                self._launch_chrome()
            except Exception as e:
                log.error(f"Failed to launch Chrome: {e}")
                return False
        else:
            log.info(f"Port {self.port} detected. Attempting to hijack existing session...")

        try:
            chrome_options = ChromeOptions()
            chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.port}")

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            self.browser = driver

            # Apply stealth via CDP
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                """
            })

            # Setup download directory
            t_dir = get_downloads_folder() / f"sel_map_extract_temp_{int(time.time())}"
            t_dir.mkdir(parents=True, exist_ok=True)
            self.temp_download_dir = t_dir

            driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(self.temp_download_dir)
            })

            # Check for login/bot status
            if not self._wait_for_manual_login():
                return False

            self._is_logged_in = True
            log.info("Successfully connected to Chrome.")
            return True

        except Exception as e:
            log.error(f"Failed to connect to Chrome on port {self.port}.")
            log.error(f"Error details: {e}")
            return False

    def _launch_chrome(self):
        if not os.path.exists(self.chrome_path):
            raise FileNotFoundError(f"Chrome not found at {self.chrome_path}")

        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        args = [
            self.chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*"
        ]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for i in range(10):
            time.sleep(1)
            if self._is_chrome_running():
                log.info(f"Chrome is now listening on port {self.port}.")
                return
            log.info(f"Waiting for Chrome to initialize... ({i + 1}/10)")

        raise ConnectionError(f"Chrome launched but port {self.port} stayed closed.")

    def _wait_for_manual_login(self) -> bool:
        """
        Checks if logged in; if not, waits for the user to log in manually.
        Now includes a safety check to handle the browser being closed during the wait.
        """
        driver = self.browser
        if not driver:
            return False

        check_url = "https://www.mapmyride.com/workouts/"
        log.info("Checking login status...")

        try:
            driver.get(check_url)
            time.sleep(3)
        except Exception as e:
            log.error(f"Initial login check failed: {e}")
            return False

        while True:
            try:
                curr_url = driver.current_url
                page_title = driver.title.lower()

                is_at_login = "auth/login" in curr_url
                is_cf_challenge = "just a moment..." in page_title or \
                                  ("cloudflare" in page_title and "challenge" in curr_url)

                if is_at_login or is_cf_challenge:
                    log.warning("!!! BOT DETECTION OR LOGIN REQUIRED !!!")
                    log.warning("Please solve the challenge or log in manually in the Chrome window.")
                    time.sleep(5)
                else:
                    if "mapmyride.com" in curr_url:
                        break
                    else:
                        log.warning("Browser is at an unexpected location. Please navigate to MapMyRide.")
                        time.sleep(5)
            except Exception as browser_err:
                log.error("The Chrome window appears to have been closed or lost. Aborting check.")
                log.debug(f"Technical Reason: {browser_err}")
                return False

        log.info("Login/Verification cleared. Resuming automation...")
        return True

    def fetch_workout_name(self, workout_id: str) -> str:
        if not self._ensure_login_and_browser():
            return ""
        driver = self.browser
        if not driver: return ""

        target_url = f"https://www.mapmyride.com/workout/{workout_id}"
        log.info(f"  - Scraping title for ID {workout_id}...")

        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBlockedURLs", {
                "urls": ["*maplibregl*", "*mapmy-static*", "*maps.googleapis.com*", "*doubleclick.net*"]
            })

            driver.get(target_url)
            wait = WebDriverWait(driver, 10)
            meta_element = wait.until(EC.presence_of_element_located((By.XPATH, "//meta[@property='og:title']")))
            name = meta_element.get_attribute("content")

            if name and isinstance(name, str):
                if " | MapMyRide" in name:
                    name = name.split(" | MapMyRide")[0].strip()
                if name.lower() == "mapmyride":
                    title_el = driver.find_element(By.XPATH, "//h4[contains(@class, 'workout-title')]")
                    name = title_el.text.strip()
                log.info(f"    - SUCCESS: Extracted name: {name}")
                return str(name).strip()
            return ""
        except Exception as e:
            log.error(f"  - Failed to scrape name for {workout_id}: {e}")
            return ""
        finally:
            if driver: driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": []})

    def _download_file_and_wait(self, url: str) -> Optional[Path]:
        driver = self.browser
        t_dir = self.temp_download_dir
        if not driver or not t_dir: return None

        files_before = {p.name for p in t_dir.iterdir()}
        driver.get(url)

        for _ in range(120):
            time.sleep(0.5)
            files_after = {p.name for p in t_dir.iterdir()}
            new_files = files_after - files_before
            if new_files:
                new_file_name = new_files.pop()
                if not new_file_name.endswith(('.tmp', '.crdownload')):
                    return t_dir / new_file_name
        return None

    def download_workout_list_csv(self) -> Optional[Path]:
        if not self._ensure_login_and_browser(): return None
        csv_export_url = self._config.get('urls', 'csv_export_url')
        return self._download_file_and_wait(csv_export_url)

    def download_tcx_file(self, workout_id: str) -> Optional[Path]:
        if not self._ensure_login_and_browser(): return None
        tcx_export_url_template = self._config.get('urls', 'tcx_export_url_template')
        download_url = tcx_export_url_template.format(workout_id=workout_id)
        return self._download_file_and_wait(download_url)
