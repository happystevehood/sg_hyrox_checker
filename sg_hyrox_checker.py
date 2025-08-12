import os
import json
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIGURATION ---
URL = "https://singapore.hyrox.com/checkout/hyrox-singapore-expo-season-25-26-49kevs"
KEYWORDS_TO_MONITOR = [
    "SATURDAY | 29.11.2025",
    "SUNDAY | 30.11.2025"
]
STATUS_FILE = "check_status.json"
# --- END CONFIGURATION ---

def setup_driver():
    """Configures the Selenium WebDriver for headless execution in GitHub Actions."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    # The webdriver-manager is great for local use, but in Actions we can rely on the path.
    # If running locally, you might need:
    # from webdriver_manager.chrome import ChromeDriverManager
    # service = Service(ChromeDriverManager().install())
    service = Service() # Assumes chromedriver is in the PATH
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def load_status():
    """Loads the status file or creates a default one."""
    try:
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Status file not found. Creating a new one.")
        return {keyword: {"found": False, "details": ""} for keyword in KEYWORDS_TO_MONITOR}

def save_status(status_data):
    """Saves the status data to the JSON file."""
    with open(STATUS_FILE, 'w') as f:
        json.dump(status_data, f, indent=2)

def main():
    driver = setup_driver()
    status_data = load_status()
    newly_found_details = []
    change_detected = False

    try:
        print(f"Navigating to URL: {URL}")
        driver.get(URL)

        # Wait for the main category container to be visible
        wait = WebDriverWait(driver, 20) # Wait up to 20 seconds
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
        print("Page and categories container loaded.")

        # Find all available category links on the page first
        category_links_xpath = "//a[.//div[contains(@class, 'vi-text')]]"
        available_categories = wait.until(EC.presence_of_all_elements_located((By.XPATH, category_links_xpath)))
        
        # Store the text of available categories to avoid stale element references
        category_texts = [cat.text for cat in available_categories if cat.text]
        print(f"Found available categories: {category_texts}")
        
        for keyword in KEYWORDS_TO_MONITOR:
            # Check if this is a category we are looking for AND we haven't found it before.
            if keyword in category_texts and not status_data[keyword]["found"]:
                print(f"--- Found new category: {keyword} ---")
                change_detected = True
                
                # Find the specific link again and click it
                print(f"Clicking on '{keyword}'...")
                keyword_link = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{keyword}')]")))
                driver.execute_script("arguments[0].click();", keyword_link)

                # Wait for the details page to load (e.g., for the 'Back to categories' button)
                wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Back to categories')]")))
                print("Details page loaded.")

                # Scrape the ticket details from the new view
                ticket_details = f"Details for {keyword}:\n"
                ticket_elements = driver.find_elements(By.CLASS_NAME, "ticket-type")
                if not ticket_elements:
                    ticket_details += "- No ticket information found.\n"
                else:
                    for ticket in ticket_elements:
                        try:
                            name = ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text
                            price = ticket.find_element(By.CLASS_NAME, "price").text
                            ticket_details += f"- {name} ({price})\n"
                        except Exception:
                            ticket_details += "- Error parsing a ticket item.\n"
                
                print(ticket_details)
                newly_found_details.append(ticket_details)
                status_data[keyword]["found"] = True
                status_data[keyword]["details"] = ticket_details

                # Go back to the category list to check the next keyword
                print("Navigating back to categories list...")
                back_button = driver.find_element(By.XPATH, "//button[contains(., 'Back to categories')]")
                driver.execute_script("arguments[0].click();", back_button)
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories"))) # Wait for page to be ready again

    finally:
        if change_detected:
            print("Changes detected. Saving new status.")
            save_status(status_data)
            notification_body = "New categories and ticket details found on Hyrox page:\n\n" + "\n".join(newly_found_details)
            # Set GitHub Actions outputs
            print(f"::set-output name=change_detected::true")
            print(f"::set-output name=notification_body::{notification_body}")
        else:
            print("No new categories found.")
            print(f"::set-output name=change_detected::false")

        print("Closing WebDriver.")
        driver.quit()

if __name__ == "__main__":
    main()