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
    """
    Sets an output variable for the GitHub Actions workflow.
    This uses the new recommended method of writing to a file.
    """
    # Find the output file path from the environment variables
    output_file = os.getenv('GITHUB_OUTPUT')
    if not output_file:
        print("Not in a GitHub Actions environment. Skipping output.")
        return
        
    with open(output_file, 'a') as f:
        # For multiline strings, we use a special "heredoc" format
        delimiter = f"EOF_{name.upper()}"
        f.write(f'{name}<<{delimiter}\n')
        f.write(f'{value}\n')
        f.write(f'{delimiter}\n')


def main():
    driver = setup_driver()
    status_data = load_status()
    newly_found_details = []
    change_detected = False

    try:
        print(f"Navigating to URL: {URL}")
        driver.get(URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
        print("Page and categories container loaded.")
        
        category_links_xpath = "//a[.//div[contains(@class, 'vi-text')]]"
        available_categories = wait.until(EC.presence_of_all_elements_located((By.XPATH, category_links_xpath)))
        category_texts = [cat.text for cat in available_categories if cat.text]
        print(f"Found available categories: {category_texts}")
        
        for keyword in KEYWORDS_TO_MONITOR:
            if keyword in category_texts and not status_data[keyword]["found"]:
                print(f"--- Found new category: {keyword} ---")
                change_detected = True
                
                print(f"Clicking on '{keyword}'...")
                keyword_link = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{keyword}')]")))
                driver.execute_script("arguments[0].click();", keyword_link)

                wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Back to categories')]")))
                print("Details page loaded.")
                
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

                print("Navigating back to categories list...")
                back_button = driver.find_element(By.XPATH, "//button[contains(., 'Back to categories')]")
                driver.execute_script("arguments[0].click();", back_button)
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
    finally:
        if change_detected:
            print("Changes detected. Saving new status.")
            save_status(status_data)
            notification_body = "New categories and ticket details found on Hyrox page:\n\n" + "\n".join(newly_found_details)
            
            # --- THIS IS THE NEW, CORRECT WAY TO SET OUTPUTS ---
            set_github_action_output('change_detected', 'true')
            set_github_action_output('notification_body', notification_body)
            
        else:
            print("No new categories found.")
            set_github_action_output('change_detected', 'false')
            set_github_action_output('notification_body', '')

        print("Closing WebDriver.")
        driver.quit()

if __name__ == "__main__":
    main()
