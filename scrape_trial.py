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
    Metadata Theft Strategy: Grabs the title from <meta property="og:title">
    to bypass the slow rendering of the map and UI components.
    """
    # 1. Load Configuration
    config = configparser.ConfigParser()
    config_path = 'C:/Users/krant/PycharmProjects/SelMapExtract/config.ini'

    if not Path(config_path).exists():
        log.error(f"FATAL: Configuration file not found at {config_path}")
        return

    config.read(config_path)

    # 2. Get Credentials from Environment
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

    # 4. Configure Chrome Options
    chrome_options = Options()

    # We keep it visible for this trial so you can see how quickly the
    # title is found even if the map is still spinning.
    chrome_options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 120)

    try:
        start_time = time.time()
        log.info(f"Navigating to login: {login_url}")
        driver.get(login_url)

        # 5. Perform Login
        log.info("Entering credentials...")
        email_el = wait.until(EC.presence_of_element_located((By.ID, email_id)))
        email_el.send_keys(username)

        pwd_el = driver.find_element(By.ID, pass_id)
        pwd_el.send_keys(password)

        login_button_xpath = "//button[contains(., 'Log In')]"
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, login_button_xpath)))
        login_button.click()

        wait.until(EC.url_changes(login_url))
        log.info("Login successful.")

        # 6. Navigate to the specific workout page
        log.info(f"Navigating to workout page: {target_url}")
        driver.get(target_url)

        # 7. Metadata Theft Strategy
        # We don't wait for the H4 to render. We look for the meta tag in the <head>.
        log.info("Waiting for Metadata (og:title)...")
        workout_name = "NOT_FOUND"

        try:
            # We wait for the meta tag to be present in the DOM.
            # Note: We use presence_of_element_located because meta tags are not visible.
            meta_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//meta[@property='og:title']")
            ))
            workout_name = meta_element.get_attribute("content")

            # If the metadata gives a generic MMR title, we fallback to the H4 you found.
            if not workout_name or "MapMyRide" == workout_name:
                log.info("Metadata was generic, falling back to H4 XPath...")
                title_xpath = "/html/body/div[2]/div/div[3]/div/div/div[1]/div[1]/div/div/div[2]/div[1]/div[1]/div/h4"
                title_element = wait.until(EC.visibility_of_element_located((By.XPATH, title_xpath)))
                workout_name = title_element.text.strip()

        except Exception as e:
            log.error(f"Metadata theft failed: {e}")

        end_time = time.time()
        log.info("--- TRIAL RESULTS ---")
        log.info(f"Target ID: {workout_id}")
        log.info(f"Extracted Name: {workout_name}")
        log.info(f"Time Taken: {end_time - start_time:.2f} seconds")

    except Exception as e:
        log.error(f"Trial failed: {e}")
    finally:
        log.info("Trial complete. Closing browser.")
        driver.quit()


if __name__ == "__main__":
    # Test workout ID
    run_scrape_trial("8649434867")
