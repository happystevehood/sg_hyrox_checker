import os
import json
import time
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from urllib.parse import urljoin
from PIL import Image, ImageDraw, ImageFont
# NEW: Import libraries for timezone-aware timestamps
from datetime import datetime
import pytz

# Configuration file names
TICKET_DETAILS_CONFIG = "config.json"
ON_SALE_CONFIG = "onsale_config.json"

# --- HELPER, SCRAPER, and MAIN MONITORING FUNCTIONS (No changes) ---
def setup_driver():
    chrome_options = Options(); chrome_options.add_argument("--headless"); chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage"); chrome_options.add_argument("--window-size=1920,1080")
    service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)

def send_email(subject, html_body, recipient_email, mail_username, mail_password):
    msg = MIMEMultipart('alternative'); msg['Subject'] = subject; msg['From'] = f"Hyrox Monitor Bot <{mail_username}>"; msg['To'] = recipient_email
    msg.attach(MIMEText(html_body, 'html'))
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
                    clean_price = normalize_text(ticket.find_element(By.CLASS_NAME, "price").text)
                    ticket_objects.append({"name": clean_name, "price": clean_price, "status": status})
                except Exception: continue
            current_status[keyword]["details"] = ticket_objects
            back_button_locator = (By.XPATH, "//button[contains(., 'Back to categories')]")
            back_button = wait.until(EC.element_to_be_clickable(back_button_locator))
            driver.execute_script("arguments[0].click();", back_button)
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "categories")))
    current_status["unmatched_categories"] = unmatched_categories
    return current_status

def _process_vivenu_v2(site_config, driver):
    keywords = site_config['keywords']; exclude_prefixes = site_config.get("exclude_prefixes", [])
    current_status = {keyword: {"found": True, "details": []} for keyword in keywords}
    wait = WebDriverWait(driver, 15)
    json_data = None
    try:
        script_element = wait.until(EC.presence_of_element_located((By.ID, "__NEXT_DATA__")))
        json_data = json.loads(script_element.get_attribute("textContent"))
    except TimeoutException:
        print("Fallback: Clicking 'Buy tickets' to load data...")
        buy_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Buy tickets')]")))
        driver.execute_script("arguments[0].click();", buy_button)
        time.sleep(3)
        object_element = wait.until(EC.presence_of_element_located((By.ID, "sellmodal-anchor")))
        checkout_path = object_element.get_attribute("data")
        if not checkout_path: raise Exception("Could not find checkout path.")
        checkout_url = urljoin(driver.current_url, checkout_path)
        print(f"Navigating directly to checkout URL: {checkout_url}")
        driver.get(checkout_url)
        script_element = wait.until(EC.presence_of_element_located((By.ID, "__NEXT_DATA__")))
        json_data = json.loads(script_element.get_attribute("textContent"))
    tickets_list = json_data.get("props", {}).get("pageProps", {}).get("shop", {}).get("tickets", [])
    currency_symbol = json_data.get("props", {}).get("pageProps", {}).get("seller", {}).get("currency", "$")
    for ticket in tickets_list:
        status = "Available" if ticket.get("active") else "Sold out"
        if status == "Sold out": continue
        clean_name = normalize_text(ticket.get("name", ""))
        if ticket.get("styleOptions", {}).get("hiddenInSelectionArea"): continue
        if any(clean_name.startswith(prefix) for prefix in exclude_prefixes): continue
        price = ticket.get("displayPrice")
        for keyword in keywords:
            if keyword.lower() in clean_name.lower():
                current_status[keyword]["details"].append({"name": clean_name, "price": f"{currency_symbol}{price}" if price is not None else "N/A", "status": status})
                break
    return current_status

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

# --- GRAPHICAL MATRIX GENERATION (Updated with timestamp) ---
def generate_availability_matrix():
    print("Generating graphical availability matrix...")
    DISPLAY_CATEGORIES = [
        "HYROX PRO WOMEN", "HYROX PRO MEN", "HYROX WOMEN", "HYROX MEN",
        "HYROX PRO DOUBLES WOMEN", "HYROX PRO DOUBLES MEN",
        "HYROX DOUBLES WOMEN", "HYROX DOUBLES MIXED", "HYROX DOUBLES MEN",
        "HYROX WOMENS RELAY", "HYROX MENS RELAY", "HYROX MIXED RELAY"
    ]
    MATCHING_CATEGORIES = sorted(DISPLAY_CATEGORIES, key=len, reverse=True)
    CELL_SIZE = 40; COL_HEADER_HEIGHT = 150; ROW_HEADER_WIDTH = 250; PADDING = 20
    FONT_SIZE = 14; AVAILABLE_COLOR = "#77DD77"; UNAVAILABLE_COLOR = "#FF6961"
    GRID_COLOR = "#D3D3D3"; TEXT_COLOR = "#000000"; BG_COLOR = "#FFFFFF"
    try:
        with open(TICKET_DETAILS_CONFIG, 'r', encoding='utf-8') as f: sites = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: Could not find '{TICKET_DETAILS_CONFIG}'. Aborting."); return
    site_names = [site['name'] for site in sites]
    matrix_data = {name: {cat: False for cat in DISPLAY_CATEGORIES} for name in site_names}
    for site in sites:
        site_name, status_file = site['name'], site['status_file']
        try:
            with open(status_file, 'r', encoding='utf-8') as f: status_data = json.load(f)
            for data in status_data.values():
                if "details" not in data: continue
                for ticket in data.get("details", []):
                    ticket_name = ticket.get("name", "")
                    normalized_ticket_name = _normalize_for_matrix(ticket_name)
                    for cat_to_check in MATCHING_CATEGORIES:
                        normalized_cat_to_check = _normalize_for_matrix(cat_to_check)
                        if normalized_cat_to_check in normalized_ticket_name:
                            matrix_data[site_name][cat_to_check] = True
                            break
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not process '{status_file}' for '{site_name}'. Reason: {e}")

    img_width = ROW_HEADER_WIDTH + (len(site_names) * CELL_SIZE) + PADDING * 2
    img_height = COL_HEADER_HEIGHT + (len(DISPLAY_CATEGORIES) * CELL_SIZE) + PADDING * 2
    
    try:
        font = ImageFont.truetype("arial.ttf", FONT_SIZE)
        timestamp_font = ImageFont.truetype("arial.ttf", FONT_SIZE - 2) # Slightly smaller font for timestamp
    except IOError:
        print("Warning: Arial font not found. Using default font."); font = ImageFont.load_default(); timestamp_font = font

    img = Image.new('RGB', (img_width, img_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    for i, name in enumerate(site_names):
        x = ROW_HEADER_WIDTH + (i * CELL_SIZE) + (CELL_SIZE / 2) + PADDING; y = COL_HEADER_HEIGHT - 10 + PADDING
        txt = Image.new('L', (COL_HEADER_HEIGHT, FONT_SIZE + 10)); d = ImageDraw.Draw(txt)
        d.text((0, 0), name, font=font, fill=255)
        w = txt.rotate(90, expand=1)
        img.paste(TEXT_COLOR, (int(x - w.size[0]/2), int(y - w.size[1])), w)

    for i, category in enumerate(DISPLAY_CATEGORIES):
        x = PADDING; y = COL_HEADER_HEIGHT + (i * CELL_SIZE) + (CELL_SIZE / 2) + PADDING
        draw.text((x, y), category, font=font, fill=TEXT_COLOR, anchor="lm")
        
    for row_idx, category in enumerate(DISPLAY_CATEGORIES):
        y1 = COL_HEADER_HEIGHT + (row_idx * CELL_SIZE) + PADDING; y2 = y1 + CELL_SIZE
        for col_idx, site_name in enumerate(site_names):
            x1 = ROW_HEADER_WIDTH + (col_idx * CELL_SIZE) + PADDING; x2 = x1 + CELL_SIZE
            is_available = matrix_data[site_name][category]
            color = AVAILABLE_COLOR if is_available else UNAVAILABLE_COLOR
            draw.rectangle([x1, y1, x2, y2], fill=color, outline=GRID_COLOR)

    # --- *** NEW: Add timestamp to the image *** ---
    try:
        mst_tz = pytz.timezone('Asia/Kuala_Lumpur')
        mst_now = datetime.now(mst_tz)
        timestamp_str = mst_now.strftime("%y:%m:%d %H:%M MST")
        draw.text((img_width - PADDING, PADDING), timestamp_str, font=timestamp_font, fill=TEXT_COLOR, anchor="ra")
    except Exception as e:
        print(f"Warning: Could not generate or draw timestamp. Error: {e}")
            
    output_filename = "availability_matrix.png"
    img.save(output_filename)
    print(f"\nMatrix image generated and saved as '{output_filename}'")

# --- MAIN ORCHESTRATOR (No changes) ---
def main():
    mail_username = os.getenv('MAIL_USERNAME'); mail_password = os.getenv('MAIL_PASSWORD')
    if not (mail_username and mail_password):
        print("Warning: Email notifications will be skipped.")
    at_least_one_change = False
    try:
        with open(TICKET_DETAILS_CONFIG, 'r', encoding='utf-8') as f: ticket_sites = json.load(f)
        for site in ticket_sites:
            try:
                result = process_ticket_details_site(site)
                if result.get("change_detected"):
                    at_least_one_change = True
                    if mail_username and mail_password and result['site_config'].get("email_to"):
                        s_config = result['site_config']; prev = result['previous_status']; curr = result['current_status']
                        subject = f"[{s_config['name']}] Hyrox Status Change Detected"
                        html_body = f"""<html><head><style>body{{font-family:sans-serif;}}pre{{background-color:#f4f4f4;padding:1em;border:1px solid #ddd;border-radius:5px;}}</style></head><body>
                        <p>A change was detected on <a href="{s_config['url']}">{s_config['name']}</a></p>
                        <h2>New Status:</h2><pre><code>{json.dumps(curr, indent=2, ensure_ascii=False)}</code></pre><hr>
                        <h2>Previous Status:</h2><pre><code>{json.dumps(prev, indent=2, ensure_ascii=False)}</code></pre></body></html>"""
                        send_email(subject, html_body, s_config['email_to'], mail_username, mail_password)
            except Exception as e: print(f"FATAL ERROR processing site {site.get('name', 'Unknown')}: {e}")
    except FileNotFoundError: print(f"Info: '{TICKET_DETAILS_CONFIG}' not found, skipping.")
    except Exception as e: print(f"FATAL ERROR during ticket detail processing: {e}")
    try:
        with open(ON_SALE_CONFIG, 'r', encoding='utf-8') as f: on_sale_sites = json.load(f)
        on_sale_config_updated = False
        for site in on_sale_sites:
            try:
                result = process_on_sale_site(site)
                if result.get("change_detected"):
                    at_least_one_change = True; on_sale_config_updated = True
                    if mail_username and mail_password and result['site_config'].get("email_to"):
                        s_config = result['site_config']
                        subject = f"[{s_config['name']}] Hyrox Tickets are ON SALE!"
                        html_body = f"""<html><body><p>Tickets for <b>{s_config['name']}</b> are now on sale.
                        <br><br>Check the page here: <a href="{s_config['url']}">{s_config['url']}</a></p></body></html>"""
                        send_email(subject, html_body, s_config['email_to'], mail_username, mail_password)
            except Exception as e: print(f"FATAL ERROR processing on-sale site {site.get('name', 'Unknown')}: {e}")
        if on_sale_config_updated:
            print(f"Updating '{ON_SALE_CONFIG}' with new 'on_sale' statuses.")
            with open(ON_SALE_CONFIG, 'w', encoding='utf-8') as f: json.dump(on_sale_sites, f, indent=2, ensure_ascii=False)
    except FileNotFoundError: print(f"Info: '{ON_SALE_CONFIG}' not found, skipping.")
    except Exception as e: print(f"FATAL ERROR in on-sale processing: {e}")
    if at_least_one_change: print("::set-output name=changes_detected::true")

if __name__ == "__main__":
    if "--matrix" in sys.argv:
        generate_availability_matrix()
    else:
        main()