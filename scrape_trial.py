import configparser
import os
import time
import logging
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Setup logging to match your project's style
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


def run_scrape_trial(workout_id):
    """
    Standalone trial to scrape the 'Proper Name' from a workout page.
    No blocking enabled - focus is on successful data extraction using
    the user-provided absolute XPath.
    """
    # 1. Load Configuration
    config = configparser.ConfigParser()
    config_path = 'C:/Users/krant/PycharmProjects/SelMapExtract/config.ini'

    if not Path(config_path).exists():
        log.error(f"FATAL: Configuration file not found at {config_path}")
        return

    config.read(config_path)

    # 2. Get Credentials from Environment (matching your client.py pattern)
    username = os.environ.get('MAPMYRIDE_USERNAME')
    password = os.environ.get('MAPMYRIDE_PASSWORD')

    if not username or not password:
        log.error("FATAL: Environment variables MAPMYRIDE_USERNAME or MAPMYRIDE_PASSWORD are not set.")
        return

    # 3. Pull Selectors and URLs from Config
    login_url = config.get('urls', 'login_url')
    email_id = config.get('selectors', 'email_input_id', fallback='email')
    pass_id = config.get('selectors', 'password_input_id', fallback='password')
    target_url = f"https://www.mapmyride.com/workout/{workout_id}"

    # 4. Configure Chrome Options (Clean Slate)
    chrome_options = Options()

    # Run in headful mode (visible) so you can solve ReCAPTCHA or observe the load
    chrome_options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=chrome_options)
    # Using your project's 120s timeout to account for manual ReCAPTCHA solving
    wait = WebDriverWait(driver, 120)

    try:
        start_time = time.time()
        log.info(f"Navigating to login: {login_url}")
        driver.get(login_url)

        # 5. Perform Login using project-standard patterns
        log.info("Entering credentials...")
        email_el = wait.until(EC.presence_of_element_located((By.ID, email_id)))
        email_el.send_keys(username)

        pwd_el = driver.find_element(By.ID, pass_id)
        pwd_el.send_keys(password)

        # Mirroring the login button click logic from your project
        login_button_xpath = "//button[contains(., 'Log In')]"
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, login_button_xpath)))
        login_button.click()

        # Wait for transition away from login page
        wait.until(EC.url_changes(login_url))
        log.info("Login successful.")

        # 6. Navigate to the specific workout page (NO BLOCKING)
        log.info(f"Navigating to workout page: {target_url}")
        driver.get(target_url)

        # 7. Extract the Title using the provided Absolute XPath
        log.info("Waiting for Title element (H4 via absolute XPath)...")
        title_xpath = "/html/body/div[2]/div/div[3]/div/div/div[1]/div[1]/div/div/div[2]/div[1]/div[1]/div/h4"
        workout_name = "NOT_FOUND"

        try:
            # We use visibility_of_element_located to ensure the title text is actually there
            title_element = wait.until(EC.visibility_of_element_located((By.XPATH, title_xpath)))
            workout_name = title_element.text.strip()
        except Exception as e:
            log.error(f"Could not find title element using XPath: {e}")

        end_time = time.time()
        log.info("--- TRIAL RESULTS ---")
        log.info(f"Target ID: {workout_id}")
        log.info(f"Extracted Name: {workout_name}")
        log.info(f"Time Taken: {end_time - start_time:.2f} seconds")

    except Exception as e:
        log.error(f"Trial failed: {e}")
    finally:
        log.info("Trial complete. Browser will close in 10 seconds.")
        time.sleep(10)
        driver.quit()


if __name__ == "__main__":
    # Test workout ID provided
    run_scrape_trial("8649434867")
