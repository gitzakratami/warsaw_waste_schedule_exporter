import os
import time
import datetime
import pickle
import json
import glob
import threading
import math
import fitz  # PyMuPDF
from flask import Flask, render_template, request, jsonify

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Google Calendar API
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

app = Flask(__name__)

# --- KONFIGURACJA ---
TARGET_URL = "https://warszawa19115.pl/harmonogramy-wywozu-odpadow"
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_NAME = "Wywóz Śmieci"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pickle"
STATE_FILE = "last_state.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

MONTH_MAP = {
    'stycznia': 1, 'lutego': 2, 'marca': 3, 'kwietnia': 4, 'maja': 5, 'czerwca': 6,
    'lipca': 7, 'sierpnia': 8, 'września': 9, 'października': 10, 'listopada': 11, 'grudnia': 12,
    'styczeń': 1, 'luty': 2, 'marzec': 3, 'kwiecień': 4, 'maj': 5, 'czerwiec': 6,
    'lipiec': 7, 'sierpień': 8, 'wrzesień': 9, 'październik': 10, 'listopad': 11, 'grudzień': 12
}

WASTE_COLORS = {
    "Papier": "7", 
    "Metale i tworzywa sztuczne": "5", 
    "Szkło": "10",
    "Bio": "8", 
    "Zmieszane": "8", 
    "Zielone": "2"
}

# --- GLOBALNY STAN POSTĘPU ---
progress_state = {
    "status": "idle",
    "percent": 0,
    "message": "Gotowy",
    "result": None
}
progress_lock = threading.Lock()

def update_progress(percent, message, status="running"):
    with progress_lock:
        progress_state["percent"] = percent
        progress_state["message"] = message
        progress_state["status"] = status

def reset_progress():
    with progress_lock:
        progress_state["percent"] = 0
        progress_state["message"] = "Inicjalizacja..."
        progress_state["status"] = "running"
        progress_state["result"] = None

# --- LOGIKA PRZETWARZANIA PDF (POPRAWIONA WERSJA) ---

def color_distance(c1, c2):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def find_matching_fraction(pix, bbox, legend):
    # Margines 2px
    start_x = max(0, int(bbox.x0) + 2)
    end_x = min(pix.width, int(bbox.x1) - 2)
    start_y = max(0, int(bbox.y0) + 2)
    end_y = min(pix.height, int(bbox.y1) - 2)

    if start_x >= end_x or start_y >= end_y:
        return None

    for x in range(start_x, end_x, 2):
        for y in range(start_y, end_y, 2):
            pixel_color = pix.pixel(x, y)
            if sum(pixel_color) > 700: continue # Ignoruj jasne tła

            for item in legend:
                if color_distance(pixel_color, item["color"]) < 45:
                    return item["name"]
    return None

def process_pdf_labels(input_pdf_path, output_pdf_path):
    """Nakłada etykiety na PDF (Wersja z poprawną detekcją kolizji)."""
    try:
        doc = fitz.open(input_pdf_path)
        
        FONT_SIZE = 10
        
        # --- PRZYGOTOWANIE CZCIONKI ---
        # 1. Próba Windows (dla testów lokalnych)
        font_path = "C:/Windows/Fonts/arialbd.ttf"
        
        # 2. Jeśli nie ma (jesteśmy na Linuxie/Dockerze), użyj systemowej
        if not os.path.exists(font_path):
            font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        
        custom_font = None
        use_custom_font = False

        if os.path.exists(font_path):
            try:
                custom_font = fitz.Font(fontfile=font_path)
                use_custom_font = True
            except Exception as e:
                print(f"[PDF] Błąd ładowania czcionki: {e}")
        else:
            print(f"[PDF] UWAGA: Nie znaleziono czcionki: {font_path}")

        # DEFINICJA KOLORÓW
        legend = [
            {"name": "ZIELONE",         "color": (83, 88, 90)},
            {"name": "ZMIESZANE",       "color": (33, 35, 35)},
            {"name": "PAPIER",          "color": (0, 95, 170)},
            {"name": "SZKŁO",           "color": (45, 160, 45)},
            {"name": "PLASTIK",         "color": (255, 205, 0)},
            {"name": "PLASTIK",         "color": (245, 170, 0)},
            {"name": "PLASTIK",         "color": (230, 150, 0)},
            {"name": "SKIP",            "color": (140, 90, 60)}, 
            {"name": "SKIP",            "color": (110, 70, 40)}, 
            {"name": "SKIP",            "color": (230, 90, 20)}, 
        ]

        for page in doc:
            pix = page.get_pixmap()
            images_info = page.get_image_info(xrefs=True)
            
            page_height = page.rect.height
            legend_cutoff_y = page_height * 0.9

            writer = fitz.TextWriter(page.rect)
            
            page_icons = []
            for img in images_info:
                bbox = fitz.Rect(img['bbox'])
                if bbox.width < 8 or bbox.width > 80: continue
                if bbox.y0 > legend_cutoff_y: continue

                label = find_matching_fraction(pix, bbox, legend)
                if label:
                    page_icons.append({"rect": bbox, "label": label})

            for icon in page_icons:
                if icon["label"] == "SKIP": continue

                text_str = icon["label"]
                
                # --- POMIARY ---
                if use_custom_font:
                    text_len = custom_font.text_length(text_str, fontsize=FONT_SIZE)
                else:
                    # Fallback dla braku czcionki
                    text_len = fitz.get_text_length(text_str, fontsize=FONT_SIZE, fontname="Helvetica-Bold")

                right_edge_of_text = icon["rect"].x0 - 5
                
                # --- KOLIZJE (Poprawiona logika) ---
                collision = True
                while collision:
                    collision = False
                    text_rect = fitz.Rect(
                        right_edge_of_text - text_len, 
                        icon["rect"].y0,               
                        right_edge_of_text,            
                        icon["rect"].y1                
                    )

                    for obstacle in page_icons:
                        if obstacle is icon: continue 
                        if text_rect.intersects(obstacle["rect"]):
                            # Przesuwamy w lewo od przeszkody
                            right_edge_of_text = obstacle["rect"].x0 - 5
                            collision = True
                            break 

                final_x = right_edge_of_text - text_len
                final_y = icon["rect"].y1 - 2
                
                # --- ZAPIS ---
                if use_custom_font:
                    writer.append((final_x, final_y), text_str, font=custom_font, fontsize=FONT_SIZE)
                else:
                    # Metoda awaryjna (może nie obsługiwać PL znaków idealnie, ale zadziała)
                    page.insert_text((final_x, final_y), text_str, fontsize=FONT_SIZE, fontname="Helvetica-Bold", color=(0,0,0))

            if use_custom_font:
                writer.write_text(page, color=(0, 0, 0))

        doc.save(output_pdf_path)
        doc.close()
        return True
    except Exception as e:
        print(f"[PDF ERROR] {e}")
        return False

# --- FUNKCJE POMOCNICZE I STARTOWE ---

def parse_polish_date(date_text):
    try:
        parts = date_text.lower().split()
        if len(parts) < 2: return None
        day = int(parts[0])
        month = MONTH_MAP.get(parts[1])
        if not month: return None
        now = datetime.datetime.now()
        year = now.year
        if now.month >= 11 and month <= 2: year += 1
        return datetime.date(year, month, day)
    except: return None

def get_google_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
    return build('calendar', 'v3', credentials=creds)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {
        "schedule": [], "logs": [], 
        "pdf_available": False, "pdf_labeled_available": False, # Nowa flaga
        "auto_mode": False, "last_auto_run": "", "saved_address": "Marszałkowska 1"
    }

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except: pass

def run_full_process(address, allowed_types):
    current_state = load_state()
    
    results = {
        "status": "success", "logs": [], "added_events": 0, "schedule": [],
        "pdf_available": False, "pdf_labeled_available": False,
        "auto_mode": current_state.get("auto_mode", False),
        "last_auto_run": current_state.get("last_auto_run", ""),
        "saved_address": address
    }

    def log(msg):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")
        results["logs"].append(f"[{timestamp}] {msg}")

    try:
        update_progress(5, "Uruchamianie przeglądarki...")
        log(f"Start: {address}")
        
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        
        prefs = {"download.default_directory": STATIC_DIR, "download.prompt_for_download": False, "plugins.always_open_pdf_externally": True}
        chrome_options.add_experimental_option("prefs", prefs)
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        schedule_data = [] 
        
        try:
            update_progress(15, "Łączenie ze stroną...")
            driver.get(TARGET_URL)
            wait = WebDriverWait(driver, 20)

            try:
                WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Zgoda na wszystkie')]"))).click()
            except: pass

            update_progress(25, "Wyszukiwanie adresu...")
            input_el = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
            input_el.clear()
            input_el.send_keys(address)
            time.sleep(1.5)

            try:
                wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.yui3-aclist-item"))).click()
            except: raise Exception("Nie znaleziono adresu")

            wait.until(EC.element_to_be_clickable((By.ID, "buttonNext"))).click()
            time.sleep(3)

            # --- PDF ---
            update_progress(40, "Pobieranie PDF...")
            try:
                # Czyścimy stare PDF
                for f in glob.glob(os.path.join(STATIC_DIR, "*.pdf")): os.remove(f)
                
                wait.until(EC.element_to_be_clickable((By.ID, "downloadPdfLink"))).click()
                
                timeout = 15
                downloaded_file = None
                while timeout > 0:
                    files = glob.glob(os.path.join(STATIC_DIR, "*.pdf"))
                    if files:
                        downloaded_file = files[0]
                        if not downloaded_file.endswith(".crdownload"): break
                    time.sleep(1)
                    timeout -= 1
                
                if downloaded_file:
                    original_pdf = os.path.join(STATIC_DIR, "harmonogram.pdf")
                    labeled_pdf = os.path.join(STATIC_DIR, "harmonogram_opisany.pdf")
                    
                    # Zmiana nazwy pobranego
                    if os.path.exists(original_pdf) and original_pdf != downloaded_file: os.remove(original_pdf)
                    os.rename(downloaded_file, original_pdf)
                    results["pdf_available"] = True
                    log("PDF pobrany.")

                    # --- GENEROWANIE OPISANEGO PDF ---
                    update_progress(50, "Generowanie opisów na PDF...")
                    log("Nakładanie etykiet na PDF...")
                    if process_pdf_labels(original_pdf, labeled_pdf):
                        results["pdf_labeled_available"] = True
                        log("Utworzono PDF z opisami.")
                    else:
                        log("Błąd generowania opisów na PDF.")

            except Exception as e:
                log(f"Błąd PDF: {e}")

            # --- SCRAPING ---
            update_progress(60, "Analiza danych ze strony...")
            element_ids = {"paper-date": "Papier", "mixed-date": "Zmieszane", "metals-date": "Metale i tworzywa sztuczne", "glass-date": "Szkło", "bio-date": "Bio", "green-date": "Zielone"}
            for html_id, waste_name in element_ids.items():
                try:
                    el = driver.find_element(By.ID, html_id)
                    txt = el.text.strip()
                    if txt:
                        schedule_data.append((txt, waste_name))
                        results["schedule"].append({"dateText": txt, "wasteType": waste_name})
                except: pass

        finally:
            driver.quit()

        if not schedule_data: raise Exception("Nie udało się pobrać dat.")

        # --- GOOGLE CALENDAR ---
        update_progress(80, "Wysyłanie do Google Calendar...")
        service_google = get_google_service()
        if service_google:
            calendar_id = None
            page_token = None
            while True:
                cal_list = service_google.calendarList().list(pageToken=page_token).execute()
                for entry in cal_list['items']:
                    if entry['summary'] == CALENDAR_NAME:
                        calendar_id = entry['id']; break
                if calendar_id: break
                page_token = cal_list.get('nextPageToken')
                if not page_token: break
            
            if not calendar_id:
                created_cal = service_google.calendars().insert(body={'summary': CALENDAR_NAME, 'timeZone': 'Europe/Warsaw'}).execute()
                calendar_id = created_cal['id']

            now_iso = datetime.datetime.utcnow().isoformat() + 'Z'
            existing = service_google.events().list(calendarId=calendar_id, timeMin=now_iso, singleEvents=True).execute().get('items', [])

            count = 0
            for i, (date_text, waste_type) in enumerate(schedule_data):
                update_progress(80 + int((i/len(schedule_data))*15), f"Przetwarzanie: {waste_type}")
                if waste_type not in allowed_types: continue
                event_date = parse_polish_date(date_text)
                if not event_date: continue
                event_date_str = event_date.isoformat()
                summary = f"Odbiór: {waste_type}"
                
                is_duplicate = False
                for ev in existing:
                    if ev.get('start', {}).get('date') == event_date_str and ev.get('summary') == summary:
                        is_duplicate = True; break
                
                if not is_duplicate:
                    body = {
                        'summary': summary, 'start': {'date': event_date_str}, 'end': {'date': event_date_str},
                        'colorId': WASTE_COLORS.get(waste_type, "8"), 'transparency': 'transparent',
                        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 300}]}
                    }
                    service_google.events().insert(calendarId=calendar_id, body=body).execute()
                    count += 1
            
            results["added_events"] = count
        else:
            log("Błąd autoryzacji Google.")

        results['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_state(results)
        
        with progress_lock:
            progress_state["percent"] = 100
            progress_state["message"] = "Zakończono!"
            progress_state["status"] = "finished"
            progress_state["result"] = results

    except Exception as e:
        with progress_lock:
            progress_state["status"] = "error"
            progress_state["message"] = str(e)
            results["status"] = "error"
            results["message"] = str(e)
            progress_state["result"] = results
        save_state(results)

def auto_scheduler():
    while True:
        try:
            state = load_state()
            if state.get("auto_mode", False) and progress_state["status"] != "running":
                today = datetime.date.today().isoformat()
                if state.get("last_auto_run") != today:
                    yesterday = datetime.date.today() - datetime.timedelta(days=1)
                    should_run = False
                    for item in state.get("schedule", []):
                        ed = parse_polish_date(item['dateText'])
                        if ed and ed == yesterday: should_run = True; break
                    
                    if should_run:
                        reset_progress()
                        state['last_auto_run'] = today
                        save_state(state)
                        run_full_process(state.get("saved_address", "Marszałkowska 1"), list(WASTE_COLORS.keys()))
            time.sleep(3600)
        except: time.sleep(60)

threading.Thread(target=auto_scheduler, daemon=True).start()

@app.route('/')
def home(): return render_template('index.html')

@app.route('/api/sync', methods=['POST'])
def api_sync():
    data = request.json
    if progress_state["status"] == "running": return jsonify({"status": "error", "message": "Proces trwa"})
    reset_progress()
    threading.Thread(target=run_full_process, args=(data.get('address'), data.get('allowedTypes'))).start()
    return jsonify({"status": "started"})

@app.route('/api/progress', methods=['GET'])
def api_progress():
    with progress_lock: return jsonify(progress_state)

@app.route('/api/toggle-auto', methods=['POST'])
def toggle_auto():
    enable = request.json.get('enable', False)
    state = load_state()
    state['auto_mode'] = enable
    save_state(state)
    return jsonify({"status": "success", "auto_mode": enable})

@app.route('/api/last-state', methods=['GET'])
def last_state(): return jsonify(load_state())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)