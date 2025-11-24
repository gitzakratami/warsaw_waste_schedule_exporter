import os
import time
import datetime
import pickle
import json
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

MONTH_MAP = {
    'stycznia': 1, 'lutego': 2, 'marca': 3, 'kwietnia': 4, 'maja': 5, 'czerwca': 6,
    'lipca': 7, 'sierpnia': 8, 'września': 9, 'października': 10, 'listopada': 11, 'grudnia': 12,
    'styczeń': 1, 'luty': 2, 'marzec': 3, 'kwiecień': 4, 'maj': 5, 'czerwiec': 6,
    'lipiec': 7, 'sierpień': 8, 'wrzesień': 9, 'październik': 10, 'listopad': 11, 'grudzień': 12
}

WASTE_COLORS = {
    "Papier": "7", "Metale i tworzywa sztuczne": "5", "Szkło": "10",
    "Bio": "8", "Zmieszane": "8", "Zielone": "2", "Gabaryty": "6"
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

# --- SCRAPER (Zmodyfikowany, aby przyjmował adres) ---

def run_full_process(address):
    results = {
        "status": "success",
        "logs": [],
        "added_events": 0,
        "schedule": []
    }

    def log(msg):
        print(msg)
        results["logs"].append(msg)

    log(f"Rozpoczynam pobieranie dla: {address}")
    
    # 1. Scraping
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--log-level=3")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    schedule_data = [] # Lista tupli (data_text, typ)
    
    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 15)

        try:
            consent = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Zgoda na wszystkie')]")))
            consent.click()
        except: pass

        input_el = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
        input_el.clear()
        input_el.send_keys(address)
        time.sleep(1.5)

        try:
            suggestion = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.yui3-aclist-item")))
            suggestion.click()
        except:
            log("Nie znaleziono adresu w podpowiedziach!")
            driver.quit()
            return {"status": "error", "message": "Nie znaleziono adresu"}

        wait.until(EC.element_to_be_clickable((By.ID, "buttonNext"))).click()
        time.sleep(2)

        element_ids = {
            "paper-date": "Papier", "mixed-date": "Zmieszane", 
            "metals-date": "Metale i tworzywa sztuczne", "glass-date": "Szkło",
            "bio-date": "Bio", "bulky-date": "Gabaryty", "green-date": "Zielone"
        }

        for html_id, waste_name in element_ids.items():
            try:
                el = driver.find_element(By.ID, html_id)
                text = el.text.strip()
                if text:
                    schedule_data.append((text, waste_name))
                    results["schedule"].append({"dateText": text, "wasteType": waste_name})
            except: pass

    except Exception as e:
        log(f"Błąd Selenium: {str(e)}")
        driver.quit()
        return {"status": "error", "message": str(e)}
    
    driver.quit()

    if not schedule_data:
        log("Nie pobrano żadnych dat.")
        return results

    # 2. Google Calendar
    log("Łączenie z Google Calendar...")
    try:
        service_google = get_google_service()
        if not service_google:
            log("Błąd autoryzacji Google (brak credentials.json?)")
            return results

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
                    'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 5 * 60}]}
                }
                service_google.events().insert(calendarId=calendar_id, body=event_body).execute()
                count_added += 1

        results["added_events"] = count_added
        log(f"Dodano {count_added} nowych wydarzeń.")

    except Exception as e:
        log(f"Błąd Google API: {str(e)}")
        results["status"] = "partial_error"
        results["message"] = str(e)

    return results

# --- ENDPOINTY FLASK ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/sync', methods=['POST'])
def api_sync():
    data = request.json
    address = data.get('address', "Obozowa 90")
    
    result = run_full_process(address)
    
    # Zapiszmy ostatni stan do pliku JSON dla frontendu
    with open('last_state.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
        
    return jsonify(result)

@app.route('/api/last-state', methods=['GET'])
def last_state():
    if os.path.exists('last_state.json'):
        with open('last_state.json', 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"schedule": []})

if __name__ == '__main__':
    # Uruchamiamy serwer Flask
    print("Serwer działa na http://127.0.0.1:5000")
    app.run(debug=True, port=5000)