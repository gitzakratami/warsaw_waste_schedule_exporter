import os
import time
from datetime import datetime
import re

# Selenium - do automatyzacji przeglądarki
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementNotInteractableException
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# pdfplumber - do czytania danych z PDF
import pdfplumber

# --- KONFIGURACJA ---
# Adres, dla którego chcesz pobrać harmonogram
ADDRESS_TO_SEARCH = "MARSZAŁKOWSKA 1 00-624 Śródmieście"
TARGET_URL = "https://warszawa19115.pl/harmonogramy-wywozu-odpadow"

# Flagi działania
HEADLESS = False  # ustaw True jeśli chcesz z powrotem tryb bez okna
CLICK_COOKIES = True  # spróbuj kliknąć baner cookies jeśli występuje

# Nazwy folderów i plików
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
PDF_FILENAME = "harmonogram.pdf"
OUTPUT_FILENAME = "harmonogram.txt"
PDF_FULL_PATH = os.path.join(DOWNLOAD_DIR, PDF_FILENAME)


# --- SŁOWNIKI DO PRZETWARZANIA DANYCH ---

# Słownik mapujący znaki (ikony) z PDF na rodzaj odpadów.
# UWAGA: Te znaki zostały ustalone na podstawie analizy PDF-a i mogą się zmienić.
# Jeśli w przyszłości skrypt przestanie działać, to jest pierwsze miejsce do sprawdzenia.
ICON_MAP = {
    'a': "Papier",
    'b': "Bio",
    'c': "Zmieszane",
    'd': "Metale i tworzywa sztuczne",
    'e': "Szkło",
    'f': "Wielkogabarytowe",
    'g': "Zielone",
    'h': "Bio restauracyjne",
}

# Słownik do konwersji polskich nazw miesięcy na numery
MONTH_MAP = {
    'STYCZEŃ': 1, 'LUTY': 2, 'MARZEC': 3, 'KWIECIEŃ': 4, 'MAJ': 5, 'CZERWIEC': 6,
    'LIPIEC': 7, 'SIERPIEŃ': 8, 'WRZESIEŃ': 9, 'PAŹDZIERNIK': 10, 'LISTOPAD': 11, 'GRUDZIEŃ': 12
}

def download_schedule_pdf():
    """
    Automatyzuje proces wejścia na stronę, wpisania adresu i pobrania pliku PDF.
    """
    print("Krok 1: Pobieranie harmonogramu w formacie PDF...")

    # Konfiguracja opcji przeglądarki Chrome
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True  # Zapobiega otwieraniu PDF w przeglądarce
    })

    # Automatyczne zarządzanie sterownikiem przeglądarki
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 25)  # Czekaj maksymalnie 25 sekund na elementy

        # Spróbuj kliknąć baner cookies jeśli istnieje
        if CLICK_COOKIES:
            try:
                # Najpierw bardzo konkretny przycisk podany przez użytkownika
                try:
                    consent_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Zgoda na wszystkie']]|//span[text()='Zgoda na wszystkie']/ancestor::button"))
                    )
                    consent_btn.click()
                    print(" - Kliknięto 'Zgoda na wszystkie'")
                except Exception:
                    # Lista bardziej ogólnych selektorów
                    cookie_xpaths = [
                        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŻŹ', 'abcdefghijklmnopqrstuvwxyząćęłńóśżź'),'zgoda na wszystkie')]",
                        "//button[contains(.,'Zgoda na wszystkie')]",
                        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŻŹ', 'abcdefghijklmnopqrstuvwxyząćęłńóśżź'),'akceptuj')]",
                        "//button[contains(.,'Akceptuj') or contains(.,'ZGADZAM') or contains(.,'Accept')]",
                        "//div[contains(@class,'cookie')]//button"
                    ]
                    for cx in cookie_xpaths:
                        buttons = driver.find_elements(By.XPATH, cx)
                        if buttons:
                            try:
                                buttons[0].click()
                                print(" - Kliknięto przycisk cookies")
                                break
                            except Exception:
                                driver.execute_script("arguments[0].click();", buttons[0])
                                print(" - Kliknięto przycisk cookies (JS)")
                                break
            except Exception as e:
                print(f" - Nie kliknięto cookies (ignoruję): {e}")

        # Znajdź pole adresu i upewnij się, że jest klikalne
        address_input = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", address_input)
        try:
            address_input.clear()
            address_input.send_keys(ADDRESS_TO_SEARCH)
        except ElementNotInteractableException:
            # Fallback JS jeśli Selenium nie może wpisać
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
                address_input,
                ADDRESS_TO_SEARCH
            )
        print(f" - Wpisano adres: {ADDRESS_TO_SEARCH}")

        # Poczekaj na listę sugestii (li) i wybierz odpowiednią
        suggestions = wait.until(EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "li.yui3-aclist-item")))
        chosen = None
        wanted_upper = ADDRESS_TO_SEARCH.upper()
        for s in suggestions:
            text_upper = s.text.strip().upper()
            data_text = (s.get_attribute("data-text") or "").strip().upper()
            if text_upper.startswith(wanted_upper) or data_text.startswith(wanted_upper):
                chosen = s
                break
        if not chosen:
            # jeśli nie znaleziono dokładnego dopasowania, weź pierwszą
            chosen = suggestions[0]
        try:
            chosen.click()
        except Exception:
            driver.execute_script("arguments[0].click();", chosen)
        print(" - Wybrano sugestię adresu")

        # Fallback: jeśli pole nadal ma surową wartość i nie aktywowało się, spróbuj ENTER
        try:
            address_input.send_keys(Keys.ARROW_DOWN)
            address_input.send_keys(Keys.ENTER)
        except Exception:
            pass

        # Kliknij przycisk "Dalej"
        next_button = wait.until(EC.element_to_be_clickable((By.ID, "buttonNext")))
        next_button.click()
        print(" - Kliknięto 'Dalej'")

        # Znajdź i kliknij link do pobrania PDF
        download_link = wait.until(EC.element_to_be_clickable((By.ID, "downloadPdfLink")))
        download_link.click()
        print(" - Kliknięto 'Pobierz harmonogram'")

        # Poczekaj, aż plik zostanie pobrany
        timeout = 30  # Maksymalnie 30 sekund na pobranie
        end_time = time.time() + timeout
        while not os.path.exists(PDF_FULL_PATH):
            time.sleep(1)
            if time.time() > end_time:
                raise Exception("Nie udało się pobrać pliku PDF w określonym czasie.")
        
        print(" - Plik PDF został pomyślnie pobrany.")

    finally:
        driver.quit() # Zawsze zamykaj przeglądarkę

def parse_schedule_from_pdf():
    """
    Przetwarza pobrany plik PDF, wyciągając z niego daty i rodzaje odpadów.
    """
    print("\nKrok 2: Przetwarzanie pliku PDF...")
    all_events = []
    current_year = int(datetime.now().year)

    with pdfplumber.open(PDF_FULL_PATH) as pdf:
        for page in pdf.pages:
            # Używamy niestandardowych ustawień do wykrywania tabel,
            # aby poprawnie połączyć komórki z ikonami.
            table_settings = {
                "vertical_strategy": "lines",
                "horizontal_strategy": "text",
            }
            tables = page.extract_tables(table_settings)

            for table in tables:
                month_name = table[0][0].upper() if table[0][0] else ""
                if month_name not in MONTH_MAP:
                    continue # To nie jest tabela z miesiącem
                
                month_number = MONTH_MAP[month_name]
                print(f" - Przetwarzam miesiąc: {month_name}")

                # Prosta logika do obsługi przełomu roku (zakładając, że harmonogram jest na przełomie)
                # Na przykładzie 2025/2026: LISTOPAD/GRUDZIEŃ = 2025, STYCZEŃ/LUTY = 2026
                if month_number < 6 and datetime.now().month > 6:
                     year_for_month = current_year + 1
                else:
                     year_for_month = current_year

                # Przechodzimy po wierszach tabeli, pomijając nagłówek z nazwą miesiąca
                for row in table[1:]:
                    for cell in row:
                        if not cell:
                            continue
                        
                        # Znajdź dzień miesiąca (zakładamy, że to pierwsza liczba w komórce)
                        day_match = re.search(r'^\d{1,2}', cell)
                        if not day_match:
                            continue
                        
                        day = int(day_match.group(0))
                        
                        # Znajdź wszystkie pasujące ikony w komórce
                        waste_types_found = []
                        # Używamy małych liter, ponieważ pdfplumber tak odczytuje te niestandardowe czcionki
                        cell_content_lower = cell.lower()
                        for icon, waste_type in ICON_MAP.items():
                            if icon in cell_content_lower:
                                waste_types_found.append(waste_type)
                        
                        if waste_types_found:
                            try:
                                event_date = datetime(year_for_month, month_number, day)
                                all_events.append((event_date, waste_types_found))
                            except ValueError:
                                print(f"   ! Błędna data: Dzień={day}, Miesiąc={month_name}, Rok={year_for_month}")
    
    print(" - Zakończono analizę PDF.")
    return all_events

def save_schedule_to_file(schedule_data):
    """
    Sortuje zebrane dane chronologicznie i zapisuje je do pliku tekstowego.
    """
    print("\nKrok 3: Zapisywanie harmonogramu do pliku...")

    if not schedule_data:
        print(" - Nie znaleziono żadnych terminów do zapisania.")
        return

    # Sortuj chronologicznie
    schedule_data.sort(key=lambda x: x[0])

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        for date, waste_types in schedule_data:
            date_str = date.strftime("%d-%m-%Y")
            waste_types_str = ", ".join(waste_types)
            line = f"{date_str} - {waste_types_str}\n"
            f.write(line)
    
    print(f" - Harmonogram został zapisany w pliku: {OUTPUT_FILENAME}")


def main():
    """
    Główna funkcja sterująca całym procesem.
    """
    print("--- Start: Automat do pobierania harmonogramu wywozu odpadów ---")

    # Utwórz folder na pobrane pliki, jeśli nie istnieje
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
        
    # Usuń stary plik PDF i TXT, jeśli istnieją, aby zacząć na czysto
    if os.path.exists(PDF_FULL_PATH):
        os.remove(PDF_FULL_PATH)
    if os.path.exists(OUTPUT_FILENAME):
        os.remove(OUTPUT_FILENAME)
        
    try:
        # Krok 1: Pobieranie
        download_schedule_pdf()
        
        # Krok 2: Przetwarzanie
        schedule = parse_schedule_from_pdf()
        
        # Krok 3: Zapis
        save_schedule_to_file(schedule)

    except Exception as e:
        print(f"\n[BŁĄD] Wystąpił nieoczekiwany problem: {e}")
        print("Sprawdź swoje połączenie z internetem lub czy strona 19115 nie uległa zmianie.")
    finally:
        # Sprzątanie - usuń pobrany plik PDF
        if os.path.exists(PDF_FULL_PATH):
            os.remove(PDF_FULL_PATH)
            print("\nKrok 4: Sprzątanie - usunięto tymczasowy plik PDF.")

    print("\n--- Zakończono pracę ---")


if __name__ == "__main__":
    main()
