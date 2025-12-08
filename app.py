import os
import time
import datetime
import pickle
import json
import glob
import threading
from flask import Flask, render_template, request, jsonify, send_from_directory

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

# Folder na pliki statyczne (PDF)
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
    "Papier": "7", "Metale i tworzywa sztuczne": "5", "Szkło": "10",
    "Bio": "8", "Zmieszane": "8", "Zielone": "2"
}

# --- FUNKCJE POMOCNICZE ---

def parse_polish_date(date_text):
    try:
        parts = date_text.lower().split()
        if len(parts) < 2: return None
        day = int(parts[0])
        month = MONTH_MAP.get(parts[1])
        if not month: return None
        now = datetime.datetime.now()
        year = now.year
        # Logika przełomu roku
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
        "schedule": [], 
        "logs": [], 
        "pdf_available": False, 
        "auto_mode": False,  # Domyślnie wyłączone
        "last_auto_run": ""
    }

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except: pass

# --- SCRAPER ---

def run_full_process(address, allowed_types):
    # Wczytujemy aktualny stan, żeby nie nadpisać ustawień auto_mode
    current_state = load_state()
    
    results = {
        "status": "success",
        "logs": [],
        "added_events": 0,
        "schedule": [],
        "pdf_available": False,
        "auto_mode": current_state.get("auto_mode", False),
        "last_auto_run": current_state.get("last_auto_run", "")
    }

    def log(msg):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {msg}"
        print(formatted_msg)
        results["logs"].append(formatted_msg)

    log(f"--- START SYNCHRONIZACJI ---")
    log(f"Adres: {address}")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--log-level=3")
    
    prefs = {
        "download.default_directory": STATIC_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    schedule_data = [] 
    
    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 15)

        try:
            consent = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Zgoda na wszystkie')]")))
            consent.click()
        except: pass

        log("Wyszukiwanie adresu...")
        input_el = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
        input_el.clear()
        input_el.send_keys(address)
        time.sleep(1.5)

        try:
            suggestion = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.yui3-aclist-item")))
            suggestion.click()
        except:
            log("BŁĄD: Nie znaleziono adresu w podpowiedziach!")
            driver.quit()
            return {"status": "error", "message": "Nie znaleziono adresu", "logs": results["logs"]}

        wait.until(EC.element_to_be_clickable((By.ID, "buttonNext"))).click()
        time.sleep(3)

        # PDF
        log("Pobieranie nowego pliku PDF...")
        try:
            for f in glob.glob(os.path.join(STATIC_DIR, "*.pdf")):
                os.remove(f)
            pdf_link = wait.until(EC.element_to_be_clickable((By.ID, "downloadPdfLink")))
            pdf_link.click()
            timeout = 10
            downloaded_file = None
            while timeout > 0:
                files = glob.glob(os.path.join(STATIC_DIR, "*.pdf"))
                if files:
                    downloaded_file = files[0]
                    if not downloaded_file.endswith(".crdownload"): break
                time.sleep(1)
                timeout -= 1
            if downloaded_file:
                final_path = os.path.join(STATIC_DIR, "harmonogram.pdf")
                if os.path.exists(final_path) and final_path != downloaded_file:
                    os.remove(final_path)
                os.rename(downloaded_file, final_path)
                results["pdf_available"] = True
                log("PDF pobrany.")
        except Exception as e:
            log(f"Błąd PDF: {e}")

        # HTML Scraping
        element_ids = {
            "paper-date": "Papier", "mixed-date": "Zmieszane", 
            "metals-date": "Metale i tworzywa sztuczne", "glass-date": "Szkło",
            "bio-date": "Bio", "green-date": "Zielone"
        }
        for html_id, waste_name in element_ids.items():
            try:
                el = driver.find_element(By.ID, html_id)
                text = el.text.strip()
                if text:
                    schedule_data.append((text, waste_name))
                    results["schedule"].append({"dateText": text, "wasteType": waste_name})
                    log(f" -> Znaleziono: {waste_name} ({text})")
            except: pass

    except Exception as e:
        log(f"BŁĄD Selenium: {str(e)}")
        driver.quit()
        return {"status": "error", "message": str(e), "logs": results["logs"]}
    
    driver.quit()

    if not schedule_data:
        log("Ostrzeżenie: Nie pobrano dat (może błąd strony?).")

    # Google Calendar
    log("Łączenie z Google Calendar API...")
    try:
        service_google = get_google_service()
        if service_google:
            # ... (Logika kalendarza identyczna jak wcześniej)
            calendar_id = None
            page_token = None
            while True:
                cal_list = service_google.calendarList().list(pageToken=page_token).execute()
                for entry in cal_list['items']:
                    if entry['summary'] == CALENDAR_NAME:
                        calendar_id = entry['id']
                        break
                if calendar_id: break
                page_token = cal_list.get('nextPageToken')
                if not page_token: break
            
            if not calendar_id:
                new_cal = {'summary': CALENDAR_NAME, 'timeZone': 'Europe/Warsaw'}
                created_cal = service_google.calendars().insert(body=new_cal).execute()
                calendar_id = created_cal['id']

            now_iso = datetime.datetime.utcnow().isoformat() + 'Z'
            events_result = service_google.events().list(calendarId=calendar_id, timeMin=now_iso, singleEvents=True).execute()
            existing_events = events_result.get('items', [])

            count_added = 0
            for date_text, waste_type in schedule_data:
                if waste_type not in allowed_types: continue
                event_date = parse_polish_date(date_text)
                if not event_date: continue
                
                event_date_str = event_date.isoformat()
                summary = f"Odbiór: {waste_type}"
                
                is_duplicate = False
                for event in existing_events:
                    start = event.get('start', {}).get('date')
                    if start == event_date_str and event.get('summary') == summary:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    event_body = {
                        'summary': summary,
                        'start': {'date': event_date_str},
                        'end': {'date': event_date_str},
                        'colorId': WASTE_COLORS.get(waste_type, "8"),
                        'transparency': 'transparent',
                        'reminders': {
                            'useDefault': False,
                            'overrides': [
                                {'method': 'popup', 'minutes': 5 * 60},
                                {'method': 'email', 'minutes': 5 * 60}
                            ]
                        }
                    }
                    service_google.events().insert(calendarId=calendar_id, body=event_body).execute()
                    log(f" -> DODANO: {waste_type}")
                    count_added += 1
            
            results["added_events"] = count_added
            log(f"Zakończono. Dodano {count_added} wydarzeń.")
        else:
            log("Błąd autoryzacji Google.")

    except Exception as e:
        log(f"Błąd Google API: {str(e)}")

    return results

# --- MECHANIZM AUTOMATYCZNEGO URUCHAMIANIA ---

def auto_scheduler():
    """Wątek działający w tle, sprawdzający czy trzeba uruchomić synchronizację."""
    print("[SYSTEM] Uruchomiono Auto-Scheduler.")
    while True:
        try:
            state = load_state()
            
            # Sprawdź czy tryb AUTO jest włączony
            if state.get("auto_mode", False):
                today_str = datetime.date.today().isoformat()
                last_run = state.get("last_auto_run", "")
                
                # Sprawdź czy dzisiaj już automat działał (żeby nie robić spamu)
                if last_run != today_str:
                    
                    # LOGIKA: Czy WCZORAJ był odbiór śmieci?
                    yesterday = datetime.date.today() - datetime.timedelta(days=1)
                    schedule = state.get("schedule", [])
                    
                    should_run = False
                    trigger_reason = ""
                    
                    for item in schedule:
                        event_date = parse_polish_date(item['dateText'])
                        if event_date and event_date == yesterday:
                            should_run = True
                            trigger_reason = f"Wczoraj ({item['dateText']}) był odbiór: {item['wasteType']}"
                            break
                    
                    if should_run:
                        print(f"[AUTO] Uruchamiam synchronizację! Powód: {trigger_reason}")
                        
                        # Pobierz ostatnio użyty adres i frakcje (lub domyślne)
                        # Tutaj uproszczenie: w prawdziwej wersji warto te dane trzymać w JSON
                        # Na razie bierzemy sztywne, ale frontend zapisuje adres w localStorage
                        # Ulepszenie: Frontend powinien wysłać te dane do zapisu w last_state.json przy każdej sync
                        # Na potrzeby demo użyjemy "Marszałkowska 1" jeśli nie mamy zapisanego
                        address = "Marszałkowska 1" # TODO: Zapisywać adres w backendzie
                        allowed = list(WASTE_COLORS.keys())
                        
                        result = run_full_process(address, allowed)
                        result['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        result['last_auto_run'] = today_str # Zapisz że dzisiaj już zrobione
                        result['auto_mode'] = True # Utrzymaj tryb auto
                        
                        save_state(result)
                    else:
                        # Debug: nic do roboty
                        pass
            
            # Sprawdzaj co godzinę (3600s)
            # Do testów możesz zmienić na np. 60 sekund
            time.sleep(3600)
            
        except Exception as e:
            print(f"[AUTO ERROR] {e}")
            time.sleep(60)

# Uruchomienie wątku w tle
scheduler_thread = threading.Thread(target=auto_scheduler, daemon=True)
scheduler_thread.start()

# --- ENDPOINTY FLASK ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/sync', methods=['POST'])
def api_sync():
    data = request.json
    address = data.get('address', "Marszałkowska 1")
    allowed_types = data.get('allowedTypes', list(WASTE_COLORS.keys()))
    
    result = run_full_process(address, allowed_types)
    result['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Zachowaj ustawienia auto
    old_state = load_state()
    result['auto_mode'] = old_state.get('auto_mode', False)
    result['last_auto_run'] = old_state.get('last_auto_run', "")
    
    save_state(result)
    return jsonify(result)

@app.route('/api/toggle-auto', methods=['POST'])
def toggle_auto():
    data = request.json
    enable = data.get('enable', False)
    
    state = load_state()
    state['auto_mode'] = enable
    save_state(state)
    
    status = "WŁĄCZONY" if enable else "WYŁĄCZONY"
    print(f"[SYSTEM] Tryb automatyczny został {status}")
    return jsonify({"status": "success", "auto_mode": enable})

@app.route('/api/last-state', methods=['GET'])
def last_state():
    return jsonify(load_state())

if __name__ == '__main__':
    print(f"Serwer działa na http://127.0.0.1:5000")
    # Ważne: use_reloader=False przy wątkach, inaczej uruchomią się dwa wątki
    app.run(debug=True, port=5000, use_reloader=False)