import os
import time
import datetime
import pickle
import json
import glob
import threading
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

# Usunięto Gabaryty
WASTE_COLORS = {
    "Papier": "7", 
    "Metale i tworzywa sztuczne": "5", 
    "Szkło": "10",
    "Bio": "8", 
    "Zmieszane": "8", 
    "Zielone": "2"
}

# --- GLOBALNY STAN POSTĘPU (Progress Bar) ---
progress_state = {
    "status": "idle",   # idle, running, finished, error
    "percent": 0,
    "message": "Gotowy",
    "result": None
}
progress_lock = threading.Lock()

def update_progress(percent, message, status="running"):
    """Aktualizuje stan paska postępu."""
    with progress_lock:
        progress_state["percent"] = percent
        progress_state["message"] = message
        progress_state["status"] = status

def reset_progress():
    """Resetuje pasek przed nowym zadaniem."""
    with progress_lock:
        progress_state["percent"] = 0
        progress_state["message"] = "Inicjalizacja..."
        progress_state["status"] = "running"
        progress_state["result"] = None

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
        # Logika przełomu roku: jeśli mamy listopad/grudzień, a data to styczeń -> to przyszły rok
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
        "auto_mode": False, 
        "last_auto_run": "",
        "saved_address": "Marszałkowska 1" # Domyślny adres dla automatu
    }

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except: pass

# --- GŁÓWNA LOGIKA (SCRAPER + SYNC) ---

def run_full_process(address, allowed_types):
    """
    Ta funkcja działa w osobnym wątku.
    Aktualizuje progress_state w trakcie działania.
    """
    current_state = load_state()
    
    # Przygotuj strukturę wyników
    results = {
        "status": "success",
        "logs": [],
        "added_events": 0,
        "schedule": [],
        "pdf_available": False,
        "auto_mode": current_state.get("auto_mode", False),
        "last_auto_run": current_state.get("last_auto_run", ""),
        "saved_address": address # Zapisujemy użyty adres dla automatu
    }

    def log(msg):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {msg}"
        print(formatted_msg)
        results["logs"].append(formatted_msg)

    try:
        update_progress(5, "Uruchamianie przeglądarki...")
        log(f"Start synchronizacji dla: {address}")
        
        # Konfiguracja Chrome
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--log-level=3")
        # Opcje dla Dockera (nie szkodzą lokalnie)
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        
        # Folder pobierania PDF
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
            update_progress(15, "Łączenie ze stroną 19115...")
            driver.get(TARGET_URL)
            wait = WebDriverWait(driver, 20)

            # Cookies
            try:
                consent = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Zgoda na wszystkie')]")))
                consent.click()
            except: pass

            update_progress(25, "Wyszukiwanie adresu...")
            input_el = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
            input_el.clear()
            input_el.send_keys(address)
            time.sleep(1.5)

            try:
                suggestion = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.yui3-aclist-item")))
                suggestion.click()
            except:
                raise Exception("Nie znaleziono adresu w podpowiedziach!")

            wait.until(EC.element_to_be_clickable((By.ID, "buttonNext"))).click()
            time.sleep(3)

            # --- PDF ---
            update_progress(40, "Pobieranie pliku PDF...")
            try:
                # Czyścimy stare PDF
                for f in glob.glob(os.path.join(STATIC_DIR, "*.pdf")):
                    os.remove(f)
                
                pdf_link = wait.until(EC.element_to_be_clickable((By.ID, "downloadPdfLink")))
                pdf_link.click()
                
                # Czekamy na plik
                timeout = 15
                downloaded_file = None
                while timeout > 0:
                    files = glob.glob(os.path.join(STATIC_DIR, "*.pdf"))
                    if files:
                        downloaded_file = files[0]
                        if not downloaded_file.endswith(".crdownload"):
                            break
                    time.sleep(1)
                    timeout -= 1
                
                if downloaded_file:
                    final_path = os.path.join(STATIC_DIR, "harmonogram.pdf")
                    if os.path.exists(final_path) and final_path != downloaded_file:
                        os.remove(final_path)
                    os.rename(downloaded_file, final_path)
                    results["pdf_available"] = True
                    log("PDF pobrany pomyślnie.")
            except Exception as e:
                log(f"Nie udało się pobrać PDF: {e}")

            # --- SCRAPING DANYCH ---
            update_progress(60, "Analiza danych ze strony...")
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

        finally:
            driver.quit()

        if not schedule_data:
            raise Exception("Nie udało się pobrać żadnych dat ze strony.")

        # --- GOOGLE CALENDAR ---
        update_progress(80, "Wysyłanie do Google Calendar...")
        
        service_google = get_google_service()
        if not service_google:
            raise Exception("Błąd autoryzacji Google (credentials.json?)")

        # Znajdź/Stwórz kalendarz
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

        # Pobierz istniejące (żeby nie dublować)
        now_iso = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service_google.events().list(calendarId=calendar_id, timeMin=now_iso, singleEvents=True).execute()
        existing_events = events_result.get('items', [])

        count_added = 0
        total_items = len(schedule_data)
        
        for i, (date_text, waste_type) in enumerate(schedule_data):
            # Aktualizacja paska postępu w pętli (od 80% do 95%)
            if total_items > 0:
                current_percent = 80 + int((i / total_items) * 15)
                update_progress(current_percent, f"Przetwarzanie: {waste_type}")

            if waste_type not in allowed_types:
                continue

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
                        'overrides': [{'method': 'popup', 'minutes': 5 * 60}]
                    }
                }
                service_google.events().insert(calendarId=calendar_id, body=event_body).execute()
                count_added += 1

        results["added_events"] = count_added
        results['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"Zakończono. Dodano {count_added} wydarzeń.")
        
        # Zapisz wynik do pliku
        save_state(results)
        
        # Zaktualizuj stan postępu na koniec
        with progress_lock:
            progress_state["percent"] = 100
            progress_state["message"] = "Zakończono pomyślnie!"
            progress_state["status"] = "finished"
            progress_state["result"] = results

    except Exception as e:
        log(f"BŁĄD KRYTYCZNY: {str(e)}")
        # Zapisz stan błędu
        with progress_lock:
            progress_state["status"] = "error"
            progress_state["message"] = str(e)
            results["status"] = "error"
            results["message"] = str(e)
            progress_state["result"] = results
        # Zapisz też do pliku, żeby po odświeżeniu było widać błąd
        save_state(results)

# --- AUTOMATYCZNE URUCHAMIANIE ---

def auto_scheduler():
    """Wątek w tle - sprawdza codziennie czy uruchomić pobieranie."""
    print("[SYSTEM] Auto-Scheduler uruchomiony.")
    while True:
        try:
            state = load_state()
            # Jeśli tryb auto jest włączony I nie trwa teraz ręczne pobieranie
            if state.get("auto_mode", False) and progress_state["status"] != "running":
                
                today_str = datetime.date.today().isoformat()
                last_run = state.get("last_auto_run", "")
                
                if last_run != today_str:
                    # Sprawdź czy wczoraj był odbiór
                    yesterday = datetime.date.today() - datetime.timedelta(days=1)
                    schedule = state.get("schedule", [])
                    
                    should_run = False
                    for item in schedule:
                        ed = parse_polish_date(item['dateText'])
                        if ed and ed == yesterday:
                            should_run = True
                            break
                    
                    if should_run:
                        print(f"[AUTO] Uruchamiam automatyczną aktualizację...")
                        address = state.get("saved_address", "Obozowa 90")
                        allowed = list(WASTE_COLORS.keys())
                        
                        # Reset stanu i uruchomienie w tym samym wątku (bo to i tak wątek tła)
                        reset_progress()
                        # Aktualizacja last_auto_run przed uruchomieniem, żeby nie zapętlić w razie błędu
                        state['last_auto_run'] = today_str
                        save_state(state)
                        
                        run_full_process(address, allowed)
            
            # Sprawdzaj co godzinę
            time.sleep(3600)
            
        except Exception as e:
            print(f"[AUTO ERROR] {e}")
            time.sleep(60)

# Uruchomienie wątku automatu
threading.Thread(target=auto_scheduler, daemon=True).start()

# --- ENDPOINTY FLASK ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Uruchamia proces w nowym wątku i od razu zwraca status."""
    data = request.json
    address = data.get('address', "Obozowa 90")
    allowed_types = data.get('allowedTypes', list(WASTE_COLORS.keys()))
    
    # Sprawdź czy już nie działa
    if progress_state["status"] == "running":
        return jsonify({"status": "error", "message": "Proces już trwa"})

    reset_progress()
    
    # Uruchomienie w tle
    thread = threading.Thread(target=run_full_process, args=(address, allowed_types))
    thread.start()
    
    return jsonify({"status": "started"})

@app.route('/api/progress', methods=['GET'])
def api_progress():
    """Frontend odpytuje ten endpoint, żeby aktualizować pasek."""
    with progress_lock:
        return jsonify(progress_state)

@app.route('/api/toggle-auto', methods=['POST'])
def toggle_auto():
    data = request.json
    enable = data.get('enable', False)
    
    state = load_state()
    state['auto_mode'] = enable
    save_state(state)
    
    return jsonify({"status": "success", "auto_mode": enable})

@app.route('/api/last-state', methods=['GET'])
def last_state():
    return jsonify(load_state())

if __name__ == '__main__':
    # host=0.0.0.0 jest wymagany dla Dockera
    print("Serwer startuje na porcie 5000...")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)