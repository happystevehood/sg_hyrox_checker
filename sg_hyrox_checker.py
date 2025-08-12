import os
import json
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIGURATION (can remain the same) ---
URL = "https://singapore.hyrox.com/checkout/hyrox-singapore-expo-season-25-26-49kevs"
KEYWORDS_TO_MONITOR = [
    "SATURDAY | 29.11.2025",
    "SUNDAY | 30.11.2025"
]
STATUS_FILE = "check_status.json"
# --- END CONFIGURATION ---

def setup_driver():
    """Configures the Selenium WebDriver for headless execution."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    service = Service()
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

def set_github_action_output(name, value):
    """Sets a multiline-safe output variable for the GitHub Actions workflow."""
    output_file = os.getenv('GITHUB_OUTPUT')
    if not output_file:
        print("Not in a GitHub Actions environment. Skipping output.")
        return
    with open(output_file, 'a') as f:
        delimiter = f"EOF_{name.upper()}"
        f.write(f'{name}<<{delimiter}\n')
        f.write(f'{value}\n')
        f.write(f'{delimiter}\n')

def main():
    driver = setup_driver()
    
    # 1. Load the state from the previous run
    previous_status = load_status()
    print("Loaded previous status:")
    print(json.dumps(previous_status, indent=2))
    
    # Initialize a new status object to be built from the live scrape
    current_status = {keyword: {"found": False, "details": ""} for keyword in KEYWORDS_TO_MONITOR}

    try:
        print(f"Navigating to URL: {URL}")
        driver.get(URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
        print("Page and categories container loaded.")
        
        category_links_xpath = "//a[.//div[contains(@class, 'vi-text')]]"
        available_categories = wait.until(EC.presence_of_all_elements_located((By.XPATH, category_links_xpath)))
        category_texts = [cat.text for cat in available_categories if cat.text]
        print(f"Found available categories on page: {category_texts}")
        
        # 2. Scrape the page and build the current_status object from scratch
        for keyword in KEYWORDS_TO_MONITOR:
            if keyword in category_texts:
                print(f"--- Processing found category: {keyword} ---")
                current_status[keyword]["found"] = True
                
                # Click the category to see details
                keyword_link = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{keyword}')]")))
                driver.execute_script("arguments[0].click();", keyword_link)
                wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Back to categories')]")))
                
                # Scrape details
                ticket_details_list = []
                ticket_elements = driver.find_elements(By.CLASS_NAME, "ticket-type")
                if not ticket_elements:
                    ticket_details_list.append("- No ticket information found.")
                else:
                    for ticket in ticket_elements:
                        try:
                            name = ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text
                            price = ticket.find_element(By.CLASS_NAME, "price").text
                            ticket_details_list.append(f"- {name} ({price})")
                        except Exception:
                            ticket_details_list.append("- Error parsing a ticket item.")
                
                current_status[keyword]["details"] = "\n".join(ticket_details_list)
                
                # Go back to the category list to process the next keyword
                back_button = driver.find_element(By.XPATH, "//button[contains(., 'Back to categories')]")
                driver.execute_script("arguments[0].click();", back_button)
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))

    finally:
        print("\nScraping complete. Comparing states.")
        print("--- Previous Status ---")
        print(json.dumps(previous_status, indent=2))
        print("\n--- Current Status ---")
        print(json.dumps(current_status, indent=2))

        # 3. Compare the old and new status objects. This detects ALL changes.
        if previous_status != current_status:
            print("\nCHANGE DETECTED!")
            
            # The notification body is now the full, nicely formatted JSON of the new state
            notification_body = f"A change was detected on the Hyrox page. The new status is:\n\n{json.dumps(current_status, indent=2)}"
            
            # Save the new state to the file for the next run
            save_status(current_status)
            print("Updated status file.")
            
            # Set GitHub Actions outputs
            set_github_action_output('change_detected', 'true')
            set_github_action_output('notification_body', notification_body)
        else:
            print("\nNo changes detected.")
            set_github_action_output('change_detected', 'false')
            set_github_action_output('notification_body', '')

        print("Closing WebDriver.")
        driver.quit()

if __name__ == "__main__":
    main()