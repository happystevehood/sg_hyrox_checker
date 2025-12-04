import os
import json
import time
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from urllib.parse import urljoin
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import pytz

# Configuration file names
TICKET_DETAILS_CONFIG = "config.json"
ON_SALE_CONFIG = "onsale_config.json"
MATRIX_STATE_FILE = "matrix_last_state.json"
MATRIX_OUTPUT_FILE = "availability_matrix.png"

# --- HELPER FUNCTIONS ---
def setup_driver():
    chrome_options = Options(); chrome_options.add_argument("--headless"); chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage"); chrome_options.add_argument("--window-size=1920,1080")
    service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)

def send_email(subject, html_body, recipient_email, mail_username, mail_password, attachment_path=None):
    msg = MIMEMultipart('alternative'); msg['Subject'] = subject; msg['From'] = f"Hyrox Monitor Bot <{mail_username}>"; msg['To'] = recipient_email
    msg.attach(MIMEText(html_body, 'html'))
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as f:
            img = MIMEImage(f.read())
            img.add_header('Content-ID', '<matrix_image>')
            msg.attach(img)
    try:
        print(f"Connecting to SMTP server for '{subject}'...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server: server.login(mail_username, mail_password); server.send_message(msg)
        print("Email sent successfully!")
    except Exception as e: print(f"Error sending email: {e}")

def normalize_text(text):
    if not isinstance(text, str): return text
    return text.encode('ascii', 'ignore').decode('utf-8').strip()

def _normalize_for_matrix(text):
    return text.upper().replace("'", "")

def set_github_output(name, value):
    github_output_path = os.getenv('GITHUB_OUTPUT')
    if github_output_path:
        with open(github_output_path, 'a') as f:
            f.write(f'{name}={value}\n')

# --- SCRAPER 1: "vivenu_v1" (No changes) ---
def _process_vivenu_v1(site_config, driver):
    keywords = site_config['keywords']; exclude_prefixes = site_config.get("exclude_prefixes", [])
    current_status = {keyword: {"found": False, "details": []} for keyword in keywords}
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
    category_links_xpath = "//a[.//div[contains(@class, 'vi-text')]]"
    available_categories_elements = wait.until(EC.presence_of_all_elements_located((By.XPATH, category_links_xpath)))
    all_page_categories = [cat.text for cat in available_categories_elements if cat.text]
    unmatched_categories = list(set(all_page_categories) - set(keywords)); unmatched_categories.sort()
    if unmatched_categories: print(f"Found new, untracked categories: {unmatched_categories}")
    for keyword in keywords:
        if keyword in all_page_categories:
            current_status[keyword]["found"] = True
            keyword_link = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{keyword}')]")))
            driver.execute_script("arguments[0].click();", keyword_link)
            wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Back to categories')]")))
            ticket_objects = []
            ticket_elements = driver.find_elements(By.CLASS_NAME, "ticket-type")
            for ticket in ticket_elements:
                try:
                    status = "Sold out" if "sold-out" in ticket.get_attribute('class') else "Available"
                    if status == "Sold out": continue
                    clean_name = normalize_text(ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text)
                    if any(clean_name.startswith(prefix) for prefix in exclude_prefixes): continue
                    ticket_objects.append({"name": clean_name, "status": status})
                except Exception: continue
            ticket_objects.sort(key=lambda x: x['name'])
            current_status[keyword]["details"] = ticket_objects
            back_button_locator = (By.XPATH, "//button[contains(., 'Back to categories')]")
            back_button = wait.until(EC.element_to_be_clickable(back_button_locator))
            driver.execute_script("arguments[0].click();", back_button)
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
    current_status["unmatched_categories"] = unmatched_categories
    return current_status

# --- SCRAPER 2: "vivenu_v2" (Rewritten to be robust and reliable) ---
def _process_vivenu_v2(site_config, driver):
    keywords = site_config['keywords']; exclude_prefixes = site_config.get("exclude_prefixes", [])
    current_status = {keyword: {"found": True, "details": []} for keyword in keywords}
    wait = WebDriverWait(driver, 20)

    # 1. Click the initial "Buy tickets" button on the main page.
    buy_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Buy tickets')]")))
    driver.execute_script("arguments[0].click();", buy_button)
    
    # 2. Wait for the iframe to be available and switch the driver's context to it.
    wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "sellmodal-anchor")))
    
    # 3. Now inside the iframe, wait for ticket elements to become visible.
    wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "ticket-type")))
    ticket_elements = driver.find_elements(By.CLASS_NAME, "ticket-type")
    
    for ticket in ticket_elements:
        try:
            # 4. Reliably check the class attribute for the "sold-out" status.
            status = "Sold out" if "sold-out" in ticket.get_attribute('class') else "Available"
            if status == "Sold out":
                continue

            clean_name = normalize_text(ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text)
            if any(clean_name.startswith(prefix) for prefix in exclude_prefixes):
                continue
            
            for keyword in keywords:
                if keyword.lower() in clean_name.lower():
                    current_status[keyword]["details"].append({"name": clean_name, "status": status})
                    break
        except Exception:
            continue

    # 5. Sort the lists for consistent comparison.
    for category_data in current_status.values():
        if "details" in category_data:
            category_data['details'].sort(key=lambda x: x['name'])
            
    return current_status


# --- MAIN ROUTER & PROCESSOR ---
def process_ticket_details_site(site_config):
    name = site_config['name']; url = site_config['url']; status_file = site_config['status_file']
    site_type = site_config.get("site_type", "vivenu_v1")
    print(f"\n--- [Ticket Details] Processing: {name} (Type: {site_type}) ---")
    try:
        with open(status_file, 'r', encoding='utf-8') as f: previous_status = json.load(f)
    except FileNotFoundError: previous_status = {}
    current_status = {}
    driver = setup_driver()
    try:
        driver.get(url)
        if site_type == "vivenu_v1":
            current_status = _process_vivenu_v1(site_config, driver)
        elif site_type == "vivenu_v2":
            current_status = _process_vivenu_v2(site_config, driver)
        else:
            print(f"ERROR: Unknown site_type '{site_type}' for '{name}'. Skipping.")
    except TimeoutException:
        print(f"ERROR: Timed out on '{name}'. Page may have changed or failed to load. Skipping.")
    except Exception as e:
        print(f"An unexpected error occurred for '{name}': {e}")
    finally:
        driver.quit()
    if previous_status != current_status and current_status:
        print(f"CHANGE DETECTED for {name}!")
        with open(status_file, 'w', encoding='utf-8') as f: json.dump(current_status, f, indent=2, ensure_ascii=False)
        print(f"Updated status file: {status_file}")
        return {"change_detected": True, "site_config": site_config, "previous_status": previous_status, "current_status": current_status}
    else:
        print(f"No changes detected for {name}."); return {"change_detected": False}

def process_on_sale_site(site_config):
    name = site_config['name']; url = site_config['url']; stored_on_sale_status = site_config['on_sale']
    print(f"\n--- [On Sale] Processing site: {name} (Stored status: {stored_on_sale_status}) ---")
    if stored_on_sale_status:
        print("Skipping check, already marked as on sale."); return {"change_detected": False}
    driver = setup_driver()
    try:
        driver.get(url)
        live_on_sale_status = "Buy Tickets here" in driver.page_source or "Buy tickets" in driver.page_source
    finally: driver.quit()
    if live_on_sale_status and not stored_on_sale_status:
        print(f"ON-SALE DETECTED for {name}!"); site_config['on_sale'] = True
        return {"change_detected": True, "site_config": site_config}
    else:
        print(f"Tickets not yet on sale for {name}."); return {"change_detected": False}

def generate_availability_matrix():
    # ... code for matrix generation is unchanged ...

def email_matrix():
    # ... code for emailing matrix is unchanged ...

# --- MAIN ORCHESTRATOR ---
def main():
    # ... code for main function is unchanged ...

if __name__ == "__main__":
    if "--matrix" in sys.argv:
        generate_availability_matrix()
    elif "--email-matrix" in sys.argv:
        email_matrix()
    else:
        main()
