import os
import time
import datetime
import pickle  # Do zapisywania tokenu sesji Google

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementNotInteractableException
from webdriver_manager.chrome import ChromeDriverManager

# Google Calendar API
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- KONFIGURACJA ---
ADDRESS_TO_SEARCH = "Obozowa 90"
TARGET_URL = "https://warszawa19115.pl/harmonogramy-wywozu-odpadow"
OUTPUT_FILENAME = "harmonogram.txt"

# Konfiguracja Google
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_NAME = "Wywóz Śmieci"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pickle"

# Mapowanie miesięcy (potrzebne do konwersji tekstu na datę)
MONTH_MAP = {
    'stycznia': 1, 'lutego': 2, 'marca': 3, 'kwietnia': 4, 'maja': 5, 'czerwca': 6,
    'lipca': 7, 'sierpnia': 8, 'września': 9, 'października': 10, 'listopada': 11, 'grudnia': 12,
    # Warianty, które mogą się pojawić (np. mianownik)
    'styczeń': 1, 'luty': 2, 'marzec': 3, 'kwiecień': 4, 'maj': 5, 'czerwiec': 6,
    'lipiec': 7, 'sierpień': 8, 'wrzesień': 9, 'październik': 10, 'listopad': 11, 'grudzień': 12
}

# Kolory w Google Calendar (orientacyjne ID)
WASTE_COLORS = {
    "Papier": "7",        # Niebieski
    "Metale i tworzywa sztuczne": "5", # Żółty
    "Szkło": "10",        # Zielony
    "Bio": "8",           # Brązowy/Szary
    "Zmieszane": "8",     # Szary/Grafitowy
    "Zielone": "2",       # Zielony jasny
    "Gabaryty": "6"       # Pomarańczowy
}

# --- CZĘŚĆ 1: SCRAPING (Pobieranie danych) ---

def fetch_waste_schedule():
    print("--- Krok 1: Pobieranie danych ze strony WWW ---")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") # Odkomentuj, żeby ukryć okno
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--log-level=3")
    # --- DODAJ TE LINIE DLA DOCKERA ---
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    schedule_data = []

    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 20)

        # 1. Cookies
        try:
            consent_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Zgoda na wszystkie')]"))
            )
            consent_btn.click()
        except:
            pass # Ignoruj brak cookies

        # 2. Adres
        print(f" - Wpisuję adres: {ADDRESS_TO_SEARCH}")
        input_el = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
        input_el.clear()
        input_el.send_keys(ADDRESS_TO_SEARCH)
        time.sleep(1) # Czekaj na sugestie

        # 3. Wybór sugestii
        suggestion = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.yui3-aclist-item")))
        suggestion.click()
        
        # 4. Dalej
        next_btn = wait.until(EC.element_to_be_clickable((By.ID, "buttonNext")))
        next_btn.click()
        time.sleep(2) # Czekaj na przeładowanie

        # 5. Pobranie danych z HTML
        print(" - Czytam daty...")
        # Mapowanie ID elementu HTML -> Nazwa frakcji
        element_ids = {
            "paper-date": "Papier",
            "mixed-date": "Zmieszane",
            "metals-date": "Metale i tworzywa sztuczne",
            # "glass-date": "Szkło",
            # "bio-date": "Bio",
            # "bulky-date": "Gabaryty",
            # "green-date": "Zielone"
        }

        for html_id, waste_name in element_ids.items():
            try:
                el = driver.find_element(By.ID, html_id)
                text = el.text.strip() # np. "4 Grudnia"
                if text:
                    schedule_data.append((text, waste_name))
                    print(f"   -> {waste_name}: {text}")
            except:
                pass # Brak daty dla tej frakcji

    except Exception as e:
        print(f"[BŁĄD Selenium]: {e}")
    finally:
        driver.quit()
    
    return schedule_data

# --- CZĘŚĆ 2: KONWERSJA DAT ---

def parse_polish_date(date_text):
    """
    Zamienia tekst '4 Grudnia' na obiekt datetime.date (YYYY-MM-DD).
    Obsługuje przełom roku.
    """
    try:
        parts = date_text.lower().split()
        if len(parts) < 2:
            return None
        
        day = int(parts[0])
        month_str = parts[1]
        
        month = MONTH_MAP.get(month_str)
        if not month:
            return None
            
        now = datetime.datetime.now()
        year = now.year
        
        # Logika przełomu roku:
        # Jeśli mamy Listopad, a pobrana data to Styczeń -> to musi być przyszły rok
        if now.month >= 11 and month <= 2:
            year += 1
        
        return datetime.date(year, month, day)
    except Exception as e:
        print(f"[BŁĄD Parsowania daty] {date_text}: {e}")
        return None

# --- CZĘŚĆ 3: GOOGLE CALENDAR SYNC ---

def get_google_service():
    """Autoryzacja i utworzenie usługi Kalendarza."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"[BŁĄD] Brak pliku {CREDENTIALS_FILE}. Pobierz go z Google Cloud Console.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build('calendar', 'v3', credentials=creds)

def sync_to_google_calendar(schedule_data):
    print("\n--- Krok 2: Synchronizacja z Google Calendar ---")
    
    service = get_google_service()
    if not service:
        return

    # 1. Znajdź lub utwórz kalendarz "Wywóz Śmieci"
    calendar_id = None
    page_token = None
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for calendar_entry in calendar_list['items']:
            if calendar_entry['summary'] == CALENDAR_NAME:
                calendar_id = calendar_entry['id']
                print(f" - Znaleziono istniejący kalendarz: {CALENDAR_NAME}")
                break
        if calendar_id:
            break
        page_token = calendar_list.get('nextPageToken')
        if not page_token:
            break
    
    if not calendar_id:
        print(f" - Tworzę nowy kalendarz: {CALENDAR_NAME}")
        new_cal = {'summary': CALENDAR_NAME, 'timeZone': 'Europe/Warsaw'}
        created_cal = service.calendars().insert(body=new_cal).execute()
        calendar_id = created_cal['id']

    # 2. Pobierz istniejące wydarzenia (żeby nie dublować)
    # Pobieramy wydarzenia od dzisiaj w przyszłość
    now_iso = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId=calendar_id, 
        timeMin=now_iso, 
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    existing_events = events_result.get('items', [])

    # 3. Dodaj nowe wydarzenia
    count_added = 0
    count_skipped = 0

    for date_text, waste_type in schedule_data:
        # Konwersja daty
        event_date = parse_polish_date(date_text)
        if not event_date:
            continue
        
        event_date_str = event_date.isoformat() # YYYY-MM-DD
        summary = f"Odbiór: {waste_type}"
        
        # Sprawdź duplikat (czy w ten dzień jest już taki sam wpis)
        is_duplicate = False
        for event in existing_events:
            # Sprawdzamy datę startu (dla wydarzeń całodniowych jest w 'date')
            start = event.get('start', {}).get('date')
            if start == event_date_str and event.get('summary') == summary:
                is_duplicate = True
                break
        
        if is_duplicate:
            print(f"   (Pominięto) {event_date_str}: {waste_type} - już istnieje")
            count_skipped += 1
            continue

        # Tworzenie wydarzenia
        color_id = WASTE_COLORS.get(waste_type, "8") # Domyślny szary
        
        event_body = {
            'summary': summary,
            'start': {'date': event_date_str},
            'end': {'date': event_date_str}, # Dla całodniowych end == start (lub start+1, Google to ogarnia)
            'colorId': color_id,
            'transparency': 'transparent', # Pokazuj jako "Dostępny" (nie blokuje kalendarza)
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 12 * 60}, # Powiadomienie 12h wcześniej
                ],
            },
        }

        try:
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
            print(f"   [DODANO] {event_date_str}: {waste_type}")
            count_added += 1
        except Exception as e:
            print(f"   [BŁĄD API] {e}")

    print(f"\nPodsumowanie: Dodano {count_added}, pominięto {count_skipped} wydarzeń.")

# --- MAIN ---

def save_to_txt(schedule_data):
    if not schedule_data: return
    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"Adres: {ADDRESS_TO_SEARCH}\n\n")
        for d, w in schedule_data:
            f.write(f"{d} - {w}\n")
    print(f" - Zapisano kopię w pliku: {OUTPUT_FILENAME}")

def main():
    # 1. Pobierz dane
    schedule = fetch_waste_schedule()
    
    if schedule:
        # 2. Zapisz txt (backup)
        save_to_txt(schedule)
        
        # 3. Wyślij do Google
        try:
            sync_to_google_calendar(schedule)
        except Exception as e:
            print(f"Błąd synchronizacji Google: {e}")
            print("Upewnij się, że masz plik 'credentials.json' w folderze.")
    else:
        print("Nie pobrano danych, kończę pracę.")

if __name__ == "__main__":
    main()