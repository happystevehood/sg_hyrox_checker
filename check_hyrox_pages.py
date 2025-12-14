import os
import json
import time
import smtplib
import sys
import re
from urllib.parse import urlparse, urlunparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import pytz

# Configuration file names
TICKET_DETAILS_CONFIG = "config.json"
ON_SALE_CONFIG = "onsale_config.json"
MATRIX_STATE_FILE = "matrix_last_state.json"
MATRIX_OUTPUT_FILE = "availability_matrix.png"

# --- HELPER FUNCTIONS ---
def setup_driver(headless=True):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)

def send_email(subject, html_body, recipient_email, mail_username, mail_password, attachment_path=None):
    if not recipient_email or not mail_username: return 
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Hyrox Monitor Bot <{mail_username}>"
    msg['To'] = recipient_email
    msg.attach(MIMEText(html_body, 'html'))
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as f:
            img = MIMEImage(f.read())
            img.add_header('Content-ID', '<matrix_image>')
            msg.attach(img)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(mail_username, mail_password)
            server.send_message(msg)
    except Exception as e: print(f"Error sending email: {e}")

def normalize_text(text):
    if not isinstance(text, str): return text
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def _normalize_for_matrix(text):
    return text.upper().replace("'", "")

def set_github_output(name, value):
    github_output_path = os.getenv('GITHUB_OUTPUT')
    if github_output_path:
        with open(github_output_path, 'a') as f:
            f.write(f'{name}={value}\n')

def clean_checkout_url(url):
    if not url: return None
    try:
        parsed = urlparse(url)
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        return clean
    except:
        return url

# --- HTML GENERATOR (UPDATED: Full List) ---
def generate_diff_html(site_config, prev_status, curr_status):
    """
    Generates an HTML table listing ALL tickets.
    Rows that changed status are highlighted.
    """
    url = site_config['url']
    name = site_config['name']
    
    prev_list = prev_status.get("General", {}).get("details", [])
    curr_list = curr_status.get("General", {}).get("details", [])
    
    # Map Name -> Status
    prev_map = {t['name']: t['status'] for t in prev_list}
    curr_map = {t['name']: t['status'] for t in curr_list}
    
    # Get all unique ticket names from both lists sorted
    all_ticket_names = sorted(list(set(prev_map.keys()) | set(curr_map.keys())))
    
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h3>Status Update for <a href="{url}" target="_blank" rel="nofollow noopener noreferrer">{name}</a></h3>
        <p>The following tickets have been detected:</p>
        <table border="1" cellpadding="8" style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">
            <tr style="background-color: #f2f2f2; text-align: left;">
                <th>Ticket Name</th>
                <th>Current Status</th>
                <th>Previous Status</th>
            </tr>
    """
    
    changes_found = False
    
    for t_name in all_ticket_names:
        p_status = prev_map.get(t_name, "N/A")
        
        if t_name in curr_map:
            c_status = curr_map[t_name]
        else:
            # If missing in current but was in prev, it's removed/sold out
            c_status = "Sold out"
            
        # Determine Styles
        row_style = ""
        status_style = ""
        
        if c_status != p_status:
            changes_found = True
            # Highlighting for changes
            if c_status.lower() == "available":
                row_style = "background-color: #d4edda;" # Green
                status_style = "color: #155724; font-weight: bold;"
            elif c_status.lower() == "sold out":
                row_style = "background-color: #f8d7da;" # Red
                status_style = "color: #721c24; font-weight: bold;"
            else:
                row_style = "background-color: #fff3cd;" # Yellow
        else:
            # Neutral style for unchanged items
            if c_status.lower() == "sold out":
                 status_style = "color: #999;" # Dimmed text for existing sold out
            
        html += f"""
        <tr style="{row_style}">
            <td>{t_name}</td>
            <td style="{status_style}">{c_status}</td>
            <td style="color: #666;">{p_status}</td>
        </tr>
        """
            
    html += """
        </table>
        <br>
        <p><small>Timestamp: {}</small></p>
    </body>
    </html>
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Return HTML only if there was at least one change to warrant an email
    return html if changes_found else None

# --- COOKIE HANDLING ---
def handle_cookies(driver):
    end_time = time.time() + 2
    while time.time() < end_time:
        try:
            host = driver.find_elements(By.ID, "usercentrics-root")
            if host:
                shadow_root = driver.execute_script("return arguments[0].shadowRoot", host[0])
                if shadow_root:
                    accept_btn = shadow_root.find_element(By.CSS_SELECTOR, "button[data-testid='uc-accept-all-button']")
                    if accept_btn.is_displayed():
                        driver.execute_script("arguments[0].click();", accept_btn)
                        return
        except: pass

        selectors = [
            "//button[contains(@class, 'rcb-btn-accept-all')]", 
            "//button[normalize-space()='Accept all']",
            "//a[normalize-space()='Accept all']"
        ]
        for xpath in selectors:
            try:
                btns = driver.find_elements(By.XPATH, xpath)
                for btn in btns:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        return 
            except: continue
        time.sleep(0.5)

# --- SHARED NAVIGATION ---

def click_back_button(driver):
    try:
        xpath = "//button[.//svg[contains(@class, 'lucide-chevron-left')] or .//div[contains(text(), 'Back')]]"
        btns = driver.find_elements(By.XPATH, xpath)
        for btn in btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                return True
    except: pass
    return False

def wait_for_view_restoration(driver, text_to_find):
    end_time = time.time() + 5
    while time.time() < end_time:
        try:
            elements = driver.find_elements(By.CLASS_NAME, "card-list-item") + \
                       driver.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
            for el in elements:
                clean_el_text = normalize_text(el.text)
                clean_target = normalize_text(text_to_find)
                if clean_target in clean_el_text and el.is_displayed():
                    return True
        except: pass
        time.sleep(0.5)
    return False

def scrape_current_view(driver, exclude_prefixes):
    tickets = []
    rows = driver.find_elements(By.CLASS_NAME, "ticket-type")
    
    if rows:
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".ticket-type button[aria-label^='Add']"))
            )
        except TimeoutException: pass
        rows = driver.find_elements(By.CLASS_NAME, "ticket-type")

    for row in rows:
        try:
            try:
                name_el = row.find_element(By.CLASS_NAME, "vi-font-semibold")
                raw_name = name_el.text
            except:
                raw_name = row.text.split('\n')[0]
            
            name = normalize_text(raw_name)
            
            if any(name.lower().startswith(p.lower()) for p in exclude_prefixes):
                continue
                
            status = "Sold out"
            try:
                add_btn = row.find_element(By.CSS_SELECTOR, "button[aria-label^='Add']")
                if add_btn.is_displayed() and add_btn.is_enabled():
                    status = "Available"
            except NoSuchElementException: pass
            
            tickets.append({"name": name, "status": status})
        except: continue
    return tickets

def traverse_menu(driver, exclude_prefixes, depth=0):
    found_tickets = []
    
    # Wait for content
    try:
        WebDriverWait(driver, 5).until(
            lambda d: d.find_elements(By.CLASS_NAME, "card-list-item") or 
                      d.find_elements(By.CLASS_NAME, "ticket-type") or
                      d.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
        )
    except TimeoutException: pass

    # 1. Leaf Node Check
    tickets_here = scrape_current_view(driver, exclude_prefixes)
    if tickets_here:
        print(f"    [Depth {depth}] Found {len(tickets_here)} tickets.")
        return tickets_here

    # 2. Find Options
    options = []
    buttons = driver.find_elements(By.CLASS_NAME, "card-list-item")
    if buttons:
        options = buttons
    else:
        cat_links = driver.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
        options = cat_links

    if not options: return []
        
    option_list = []
    for o in options:
        if not o.is_displayed(): continue
        try:
            text_div = o.find_element(By.CLASS_NAME, "vi-font-medium")
            raw = text_div.text
        except:
            raw = o.text.split('\n')[0]
            
        if raw and "Tickets available" not in raw and "Select" not in raw:
            option_list.append(raw)
            
    option_list = list(dict.fromkeys(option_list))

    for opt_text in option_list:
        clean_opt_text = normalize_text(opt_text)
        if any(clean_opt_text.lower().startswith(p.lower()) for p in exclude_prefixes):
            print(f"    [Depth {depth}] Skipping excluded: {clean_opt_text}")
            continue

        print(f"    [Depth {depth}] Clicking option: {clean_opt_text}")
        
        target = None
        
        def is_match(element):
            return clean_opt_text == normalize_text(element.text) and element.is_displayed()

        fresh_btns = driver.find_elements(By.CLASS_NAME, "card-list-item")
        for b in fresh_btns:
            if is_match(b):
                target = b
                break
        
        if not target:
            fresh_links = driver.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
            for l in fresh_links:
                if is_match(l):
                    target = l
                    break
        
        if not target:
             fresh_links = driver.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
             for l in fresh_links:
                 if clean_opt_text in normalize_text(l.text) and l.is_displayed():
                     target = l
                     break

        if target:
            try:
                driver.execute_script("arguments[0].click();", target)
                time.sleep(1.0) 
                
                results = traverse_menu(driver, exclude_prefixes, depth + 1)
                found_tickets.extend(results)
                
                if click_back_button(driver):
                    wait_for_view_restoration(driver, opt_text)
                
            except Exception as e:
                print(f"    ! Error clicking {clean_opt_text}: {e}")

    return found_tickets

def execute_checkout_scraping(driver, checkout_url, site_config):
    print(f"  > Clean Checkout URL: {checkout_url[:60]}...")
    driver.get(checkout_url)
    handle_cookies(driver)
    
    print("  > Waiting for menu content...")
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.find_elements(By.CLASS_NAME, "card-list-item") or 
                      d.find_elements(By.CLASS_NAME, "ticket-type") or
                      d.find_elements(By.XPATH, "//a[contains(@class, 'vi-rounded-lg')]")
        )
    except TimeoutException:
        print("  ! Checkout page did not load content.")
        safe_name = site_config['name'].replace(' ', '_').replace("'", "")
        driver.save_screenshot(f"debug_failed_load_{safe_name}.png")
        return {"change_detected": False}

    all_tickets = traverse_menu(driver, site_config.get("exclude_prefixes", []))
    
    # We mark "found" as True if we successfully reached the scraping stage,
    # even if all_tickets is empty (e.g. all sold out/excluded)
    current_status = {"General": {"found": True, "details": []}}
    
    if all_tickets:
        unique = {t['name']:t for t in all_tickets}.values()
        current_status["General"]["details"] = sorted(list(unique), key=lambda x: x['name'])
        print(f"  > Success! Found {len(current_status['General']['details'])} unique tickets.")
    else:
        print("  > No tickets found (All sold out or excluded).")

    status_file = site_config['status_file']
    try:
        with open(status_file, 'r', encoding='utf-8') as f: previous_status = json.load(f)
    except: previous_status = {}

    if previous_status != current_status and current_status["General"]["found"]:
        html_body = generate_diff_html(site_config, previous_status, current_status)
        
        # Only if html_body is returned (meaning at least one status change occurred)
        if html_body:
            print(f"  > CHANGE DETECTED!")
            with open(status_file, 'w', encoding='utf-8') as f: 
                json.dump(current_status, f, indent=2, ensure_ascii=False)
            return {
                "change_detected": True, 
                "site_config": site_config,
                "html_body": html_body
            }
        else:
             # Sync file if there was a structure change but no status change
             with open(status_file, 'w', encoding='utf-8') as f: 
                json.dump(current_status, f, indent=2, ensure_ascii=False)
    
    return {"change_detected": False}

# --- PROCESSOR 1: STANDARD ---
def _process_hyrox_event_page(site_config, driver):
    print(f"  > [Standard Flow] Loading event page...")
    driver.get(site_config['url'])
    handle_cookies(driver)
    
    checkout_url = None
    try:
        buy_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Buy Tickets here']"))
        )
        driver.execute_script("arguments[0].click();", buy_btn)
    except TimeoutException:
        print("    ! Could not find 'Buy Tickets here' button.")
        return {"change_detected": False}

    try:
        time.sleep(1) 
        athlete_link = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Athlete Tickets')]"))
        )
        driver.execute_script("arguments[0].click();", athlete_link)
        
        time.sleep(2)
        try:
            obj = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "sellmodal-anchor"))
            )
            raw_url = obj.get_attribute("data")
            checkout_url = clean_checkout_url(raw_url)
        except TimeoutException:
            objs = driver.find_elements(By.TAG_NAME, "object")
            for o in objs:
                if "checkout" in (o.get_attribute("data") or ""):
                    checkout_url = clean_checkout_url(o.get_attribute("data"))
                    break
    except Exception: pass
    
    if checkout_url:
        return execute_checkout_scraping(driver, checkout_url, site_config)
    else:
        print("  ! Failed to extract checkout URL.")
        return {"change_detected": False}

# --- PROCESSOR 2: INDIA ---
def _process_hyrox_event_page_india(site_config, driver):
    print(f"  > [India Flow] Loading event page...")
    driver.get(site_config['url'])
    handle_cookies(driver)
    
    checkout_url = None
    try:
        keywords = ["buy ticket", "register", "get ticket", "book now", "tickets"]
        target = None
        for kw in keywords:
            if target: break
            xpath = f"//*[(self::a or self::button or contains(@class, 'btn') or contains(@class, 'button')) and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw}')]"
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                if el.is_displayed():
                    target = el
                    break
        
        if target:
            if target.tag_name == 'a':
                href = target.get_attribute('href')
                if href and ("checkout" in href or "vivenu" in href):
                    checkout_url = clean_checkout_url(href)
            
            if not checkout_url:
                current_url = driver.current_url
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
                time.sleep(0.5)
                try: target.click()
                except: driver.execute_script("arguments[0].click();", target)
                time.sleep(3) 
                
                if driver.current_url != current_url and ("checkout" in driver.current_url or "vivenu" in driver.current_url):
                    checkout_url = clean_checkout_url(driver.current_url)
                
                if not checkout_url and len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[-1])
                    if "checkout" in driver.current_url or "vivenu" in driver.current_url:
                        checkout_url = clean_checkout_url(driver.current_url)

                if not checkout_url:
                    objs = driver.find_elements(By.ID, "sellmodal-anchor")
                    if objs:
                        data = objs[0].get_attribute("data")
                        if data: checkout_url = clean_checkout_url(data)
                    
                    if not checkout_url:
                        frames = driver.find_elements(By.TAG_NAME, "iframe")
                        for f in frames:
                            src = f.get_attribute("src")
                            if src and ("checkout" in src or "vivenu" in src):
                                checkout_url = clean_checkout_url(src)
                                break
    except Exception as e: print(f"    ! Error in India flow: {e}")

    if checkout_url:
        return execute_checkout_scraping(driver, checkout_url, site_config)
    else:
        print("  ! Failed to extract India checkout URL.")
        return {"change_detected": False}

# --- MAIN ROUTER ---
def process_ticket_details_site(site_config, driver):
    name = site_config['name']
    site_type = site_config.get("site_type", "hyrox_event_page")
    
    print(f"\n--- Processing: {name} (Type: {site_type}) ---")
    try:
        if site_type == "hyrox_event_page":
            return _process_hyrox_event_page(site_config, driver)
        elif site_type == "hyrox_event_page_india":
            return _process_hyrox_event_page_india(site_config, driver)
        else:
            print(f"  ! Unknown site_type: {site_type}")
            return {"change_detected": False}
    except Exception as e:
        print(f"  ! Unexpected error: {e}")
        return {"change_detected": False}

# --- MATRIX GENERATION ---
def generate_availability_matrix():
    print("Generating matrix...")
    DISPLAY_CATEGORIES = [
        "HYROX PRO WOMEN", "HYROX PRO MEN", "HYROX WOMEN", "HYROX MEN",
        "HYROX PRO DOUBLES WOMEN", "HYROX PRO DOUBLES MEN",
        "HYROX DOUBLES WOMEN", "HYROX DOUBLES MIXED", "HYROX DOUBLES MEN",
        "HYROX WOMENS RELAY", "HYROX MENS RELAY", "HYROX MIXED RELAY"
    ]
    MATCHING_CATEGORIES = sorted(DISPLAY_CATEGORIES, key=len, reverse=True)
    CELL_SIZE = 40; COL_HEADER_HEIGHT = 150; ROW_HEADER_WIDTH = 250; PADDING = 20
    FONT_SIZE = 14; AVAILABLE_COLOR = "#77DD77"; UNAVAILABLE_COLOR = "#FF6961"
    
    try:
        with open(TICKET_DETAILS_CONFIG, 'r') as f: config = json.load(f)
        sites = config.get("sites", [])
    except: return

    try:
        with open(MATRIX_STATE_FILE, 'r') as f: prev_matrix = json.load(f)
    except: prev_matrix = {}

    site_names = [s['name'] for s in sites]
    curr_matrix = {n: {c: False for c in DISPLAY_CATEGORIES} for n in site_names}

    for site in sites:
        try:
            with open(site['status_file'], 'r') as f: data = json.load(f)
            tickets = []
            for k, v in data.items():
                if "details" in v: tickets.extend(v["details"])
            
            for t in tickets:
                if t.get("status") == "Available":
                    norm_name = _normalize_for_matrix(t.get("name", ""))
                    for cat in MATCHING_CATEGORIES:
                        if _normalize_for_matrix(cat) in norm_name:
                            curr_matrix[site['name']][cat] = True
                            break
        except: pass

    w = ROW_HEADER_WIDTH + (len(site_names) * CELL_SIZE) + PADDING * 2
    h = COL_HEADER_HEIGHT + (len(DISPLAY_CATEGORIES) * CELL_SIZE) + PADDING * 2
    
    try: font = ImageFont.truetype("arial.ttf", FONT_SIZE)
    except: font = ImageFont.load_default()
    
    img = Image.new('RGB', (w, h), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    for i, name in enumerate(site_names):
        x = ROW_HEADER_WIDTH + (i * CELL_SIZE) + (CELL_SIZE / 2) + PADDING
        y = COL_HEADER_HEIGHT - 10 + PADDING
        txt = Image.new('L', (COL_HEADER_HEIGHT, FONT_SIZE + 10))
        d = ImageDraw.Draw(txt)
        d.text((0, 0), name, font=font, fill=255)
        r_txt = txt.rotate(90, expand=1)
        img.paste("#000000", (int(x - r_txt.size[0]/2), int(y - r_txt.size[1])), r_txt)

    for i, cat in enumerate(DISPLAY_CATEGORIES):
        draw.text((PADDING, COL_HEADER_HEIGHT + (i * CELL_SIZE) + 20 + PADDING), cat, font=font, fill="black", anchor="lm")
        
    for r, cat in enumerate(DISPLAY_CATEGORIES):
        y1 = COL_HEADER_HEIGHT + (r * CELL_SIZE) + PADDING
        for c, name in enumerate(site_names):
            x1 = ROW_HEADER_WIDTH + (c * CELL_SIZE) + PADDING
            avail = curr_matrix.get(name, {}).get(cat, False)
            color = AVAILABLE_COLOR if avail else UNAVAILABLE_COLOR
            draw.rectangle([x1, y1, x1+CELL_SIZE, y1+CELL_SIZE], fill=color, outline="#D3D3D3")
            if avail != prev_matrix.get(name, {}).get(cat, False):
                draw.text((x1+20, y1+20), "X", font=font, fill="black", anchor="mm")

    try:
        ts = datetime.now(pytz.timezone('Asia/Kuala_Lumpur')).strftime("%y:%m:%d %H:%M MST")
        draw.text((w - PADDING, PADDING), ts, font=font, fill="black", anchor="ra")
    except: pass
    
    img.save(MATRIX_OUTPUT_FILE)
    print(f"Matrix saved to {MATRIX_OUTPUT_FILE}")
    
    if curr_matrix != prev_matrix:
        with open(MATRIX_STATE_FILE, 'w') as f: json.dump(curr_matrix, f, indent=2)
        set_github_output('matrix_changed', 'true')
    else:
        set_github_output('matrix_changed', 'false')

def email_matrix():
    mail_user = os.getenv('MAIL_USERNAME'); mail_pass = os.getenv('MAIL_PASSWORD')
    if not (mail_user and mail_pass): return
    try:
        with open(TICKET_DETAILS_CONFIG, 'r') as f: rcpt = json.load(f).get("matrix_email_to")
    except: return
    
    mst = pytz.timezone('Asia/Kuala_Lumpur')
    sub = f"Hyrox Matrix - {datetime.now(mst).strftime('%Y-%m-%d')}"
    body = "<html><body><img src='cid:matrix_image'></body></html>"
    send_email(sub, body, rcpt, mail_user, mail_pass, MATRIX_OUTPUT_FILE)

# --- MAIN ---
def main(headless=True):
    mail_user = os.getenv('MAIL_USERNAME'); mail_pass = os.getenv('MAIL_PASSWORD')
    change = False
    
    driver = setup_driver(headless)
    
    try:
        with open(TICKET_DETAILS_CONFIG, 'r') as f: sites = json.load(f)["sites"]
        
        for s in sites:
            try:
                res = process_ticket_details_site(s, driver)
                if res.get("change_detected"):
                    change = True
                    if mail_user and mail_pass and res['site_config'].get("email_to"):
                        subject = f"[{s['name']}] Status Change Detected"
                        html_body = res.get("html_body", "No details available")
                        send_email(subject, html_body, res['site_config']['email_to'], mail_user, mail_pass)
            except Exception as e:
                print(f"Error processing {s['name']}: {e}")
                
    except Exception as e: print(f"Fatal Error: {e}")
    finally:
        driver.quit()
        
    if change: set_github_output('changes_detected', 'true')

if __name__ == "__main__":
    is_headless = "--visible" not in sys.argv
    if "--matrix" in sys.argv: generate_availability_matrix()
    elif "--email-matrix" in sys.argv: email_matrix()
    else: main(headless=is_headless)
