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

CONFIG_FILE = "config.json"

# --- EMAIL SENDING FUNCTION ---
def send_email(subject, html_body, recipient_email, mail_username, mail_password):
    """Sends an HTML email using credentials from environment variables."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Hyrox Monitor Bot <{mail_username}>"
    msg['To'] = recipient_email
    
    # Attach the HTML body
    msg.attach(MIMEText(html_body, 'html'))

    try:
        print(f"Connecting to SMTP server to send email to {recipient_email}...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(mail_username, mail_password)
            server.send_message(msg)
        print("Email sent successfully!")
    except Exception as e:
        print(f"Error sending email: {e}")

# --- WEB SCRAPING & PROCESSING LOGIC ---
def setup_driver():
    """Configures the Selenium WebDriver for headless execution."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)

def process_site(site_config, mail_username, mail_password):
    """Processes a single site configuration: scrapes, compares, and notifies."""
    name = site_config['name']
    url = site_config['url']
    keywords = site_config['keywords']
    status_file = site_config['status_file']
    email_to = site_config['email_to']

    print(f"\n--- Processing site: {name} ---")
    print(f"URL: {url}")
    
    # Load previous status for this specific site
    try:
        with open(status_file, 'r') as f:
            previous_status = json.load(f)
    except FileNotFoundError:
        print(f"Status file '{status_file}' not found. Creating a new default.")
        previous_status = {keyword: {"found": False, "details": []} for keyword in keywords}

    # Build the current state from scratch
    current_status = {keyword: {"found": False, "details": []} for keyword in keywords}
    driver = setup_driver()
    any_change_detected = False

    try:
        driver.get(url)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
        print("Page loaded successfully.")
        
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
                        name_text = ticket.find_element(By.CLASS_NAME, "vi-font-semibold").text
                        price = ticket.find_element(By.CLASS_NAME, "price").text
                        class_string = ticket.get_attribute('class')
                        status = "Sold out" if "sold-out" in class_string else "Available"
                        ticket_objects.append({"name": name_text, "price": price, "status": status})
                    except Exception as e:
                        print(f"Warning: Could not parse a ticket item. Error: {e}")
                
                current_status[keyword]["details"] = ticket_objects
                back_button = driver.find_element(By.XPATH, "//button[contains(., 'Back to categories')]")
                driver.execute_script("arguments[0].click();", back_button)
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))

    finally:
        driver.quit()
        print("WebDriver closed.")

    # Compare states and send notification if needed
    if previous_status != current_status:
        print(f"CHANGE DETECTED for {name}!")
        any_change_detected = True
        
        # Save the new state
        with open(status_file, 'w') as f:
            json.dump(current_status, f, indent=2)
        print(f"Updated status file: {status_file}")

        # Build and send the email
        subject = f"[{name}] Hyrox Update: Category Changes!"
        current_status_json = json.dumps(current_status, indent=2)
        previous_status_json = json.dumps(previous_status, indent=2)
        html_body = f"""
        <html><head><style>body{{font-family:sans-serif;}}pre{{background-color:#f4f4f4;padding:1em;border:1px solid #ddd;border-radius:5px;white-space:pre-wrap;word-wrap:break-word;}}code{{font-family:monospace;}}hr{{border:0;border-top:1px solid #eee;}}</style></head><body>
        <p>A change was detected on the Hyrox page: <a href="{url}">{name}</a></p>
        <h2>New Status:</h2><pre><code>{current_status_json}</code></pre><hr>
        <h2>Previous Status:</h2><pre><code>{previous_status_json}</code></pre>
        </body></html>
        """
        send_email(subject, html_body, email_to, mail_username, mail_password)
    else:
        print(f"No changes detected for {name}.")
        
    return any_change_detected


def main():
    """Main function to run the monitor."""
    mail_username = os.getenv('MAIL_USERNAME')
    mail_password = os.getenv('MAIL_PASSWORD')

    if not (mail_username and mail_password):
        print("Error: MAIL_USERNAME and MAIL_PASSWORD environment variables not set.")
        return

    try:
        with open(CONFIG_FILE, 'r') as f:
            sites_to_monitor = json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
        return
        
    at_least_one_change = False
    for site in sites_to_monitor:
        try:
            if process_site(site, mail_username, mail_password):
                at_least_one_change = True
        except Exception as e:
            print(f"FATAL ERROR processing site {site.get('name', 'Unknown')}: {e}")
            
    if at_least_one_change:
        # This special print is used to tell the workflow to commit the changes.
        print("::set-output name=changes_detected::true")

if __name__ == "__main__":
    main()