import os
import json
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configuration file names
TICKET_DETAILS_CONFIG = "config.json"
ON_SALE_CONFIG = "onsale_config.json"

# --- HELPER FUNCTIONS (Setup and Email - No changes) ---
def setup_driver():
    chrome_options = Options(); chrome_options.add_argument("--headless"); chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage"); chrome_options.add_argument("--window-size=1920,1080")
    service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)

def send_email(subject, html_body, recipient_email, mail_username, mail_password):
    msg = MIMEMultipart('alternative'); msg['Subject'] = subject; msg['From'] = f"Hyrox Monitor Bot <{mail_username}>"; msg['To'] = recipient_email
    msg.attach(MIMEText(html_body, 'html'))
    try:
        print(f"Connecting to SMTP server to send email to {recipient_email}...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server: server.login(mail_username, mail_password); server.send_message(msg)
        print("Email sent successfully!")
    except Exception as e: print(f"Error sending email: {e}")

# --- PROCESSOR 1: TICKET DETAILS CHECKER (Now manages its own driver) ---
def process_ticket_details_site(site_config):
    name = site_config['name']; url = site_config['url']; keywords = site_config['keywords']; status_file = site_config['status_file']
    print(f"\n--- [Ticket Details] Processing site: {name} ---")
    try:
        with open(status_file, 'r') as f: previous_status = json.load(f)
    except FileNotFoundError: previous_status = {keyword: {"found": False, "details": []} for keyword in keywords}
    
    current_status = {keyword: {"found": False, "details": []} for keyword in keywords}
    driver = setup_driver() # NEW: Create driver inside the function
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 20); wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
        category_links_xpath = "//a[.//div[contains(@class, 'vi-text')]]"
        available_categories = wait.until(EC.presence_of_all_elements_located((By.XPATH, category_links_xpath)))
        category_texts = [cat.text for cat in available_categories if cat.text]
        for keyword in keywords:
            if keyword in category_texts:
                current_status[keyword]["found"] = True
                keyword_link = wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{keyword}')]")))
                driver.execute_script("arguments[0].click();", keyword_link)
                wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Back to categories')]")))
                ticket_objects = []
                ticket_elements = driver.find_elements(By.CLASS_NAME, "ticket-type")
                for ticket in ticket_elements:
                    try:
                        name_text = ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text; price = ticket.find_element(By.CLASS_NAME, "price").text
                        status = "Sold out" if "sold-out" in ticket.get_attribute('class') else "Available"
                        ticket_objects.append({"name": name_text, "price": price, "status": status})
                    except Exception: continue
                current_status[keyword]["details"] = ticket_objects
                driver.find_element(By.XPATH, "//button[contains(., 'Back to categories')]").click()
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
    finally:
        driver.quit() # NEW: Quit driver at the end of this specific check

    if previous_status != current_status:
        print(f"CHANGE DETECTED for {name}!")
        with open(status_file, 'w') as f: json.dump(current_status, f, indent=2)
        print(f"Updated status file: {status_file}")
        return {"change_detected": True, "site_config": site_config, "previous_status": previous_status, "current_status": current_status}
    else: print(f"No changes detected for {name}."); return {"change_detected": False}


# --- PROCESSOR 2: ON-SALE CHECKER (Now manages its own driver) ---
def process_on_sale_site(site_config):
    name = site_config['name']; url = site_config['url']; stored_on_sale_status = site_config['on_sale']
    print(f"\n--- [On Sale] Processing site: {name} (Stored status: {stored_on_sale_status}) ---")
    if stored_on_sale_status:
        print("Skipping check, already marked as on sale."); return {"change_detected": False}
        
    driver = setup_driver() # NEW: Create driver inside the function
    try:
        driver.get(url)
        live_on_sale_status = "Buy Tickets here" in driver.page_source
    finally:
        driver.quit() # NEW: Quit driver at the end of this specific check
    
    if live_on_sale_status and not stored_on_sale_status:
        print(f"ON-SALE DETECTED for {name}!")
        site_config['on_sale'] = True
        return {"change_detected": True, "site_config": site_config}
    else:
        print(f"Tickets not yet on sale for {name}."); return {"change_detected": False}


# --- MAIN ORCHESTRATOR (Simplified: no longer manages the driver) ---
def main():
    mail_username = os.getenv('MAIL_USERNAME'); mail_password = os.getenv('MAIL_PASSWORD')
    if not (mail_username and mail_password): print("Error: MAIL_USERNAME and MAIL_PASSWORD env vars not set."); return

    at_least_one_change = False

    # 1. Process Ticket Detail Checks
    try:
        with open(TICKET_DETAILS_CONFIG, 'r') as f: ticket_sites = json.load(f)
        for site in ticket_sites:
            result = process_ticket_details_site(site) # REMOVED: driver argument
            if result.get("change_detected"):
                at_least_one_change = True
                s_config = result['site_config']; prev = result['previous_status']; curr = result['current_status']
                subject = f"[{s_config['name']}] Hyrox Status Change Detected"
                html_body = f"""<html><head><style>body{{font-family:sans-serif;}}pre{{background-color:#f4f4f4;padding:1em;border:1px solid #ddd;border-radius:5px;}}</style></head><body>
                <p>A change was detected on <a href="{s_config['url']}">{s_config['name']}</a></p>
                <h2>New Status:</h2><pre><code>{json.dumps(curr, indent=2)}</code></pre><hr>
                <h2>Previous Status:</h2><pre><code>{json.dumps(prev, indent=2)}</code></pre></body></html>"""
                send_email(subject, html_body, s_config['email_to'], mail_username, mail_password)
    except FileNotFoundError: print(f"Info: '{TICKET_DETAILS_CONFIG}' not found, skipping.")
    except Exception as e: print(f"FATAL ERROR in ticket detail processing: {e}")

    # 2. Process On-Sale Checks
    try:
        with open(ON_SALE_CONFIG, 'r') as f: on_sale_sites = json.load(f)
        on_sale_config_updated = False
        for site in on_sale_sites:
            result = process_on_sale_site(site) # REMOVED: driver argument
            if result.get("change_detected"):
                at_least_one_change = True; on_sale_config_updated = True
                s_config = result['site_config']
                subject = f"[{s_config['name']}] Hyrox Tickets are ON SALE!"
                html_body = f"""<html><body><p>Tickets for <b>{s_config['name']}</b> are now on sale.
                <br><br>Check the page here: <a href="{s_config['url']}">{s_config['url']}</a></p></body></html>"""
                send_email(subject, html_body, s_config['email_to'], mail_username, mail_password)
        if on_sale_config_updated:
            print(f"Updating '{ON_SALE_CONFIG}' with new 'on_sale' statuses.")
            with open(ON_SALE_CONFIG, 'w') as f: json.dump(on_sale_sites, f, indent=2)
    except FileNotFoundError: print(f"Info: '{ON_SALE_CONFIG}' not found, skipping.")
    except Exception as e: print(f"FATAL ERROR in on-sale processing: {e}")
        
    if at_least_one_change: print("::set-output name=changes_detected::true")

if __name__ == "__main__":
    main()
