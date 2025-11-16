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

# --- KONFIGURACJA ---
# Adres, dla którego chcesz pobrać harmonogram
ADDRESS_TO_SEARCH = "Obozowa 90"
TARGET_URL = "https://warszawa19115.pl/harmonogramy-wywozu-odpadow"

# Flagi działania
HEADLESS = False  # ustaw True jeśli chcesz z powrotem tryb bez okna
CLICK_COOKIES = True  # spróbuj kliknąć baner cookies jeśli występuje
ACTION_DELAY = 0.8  # sekundy pauzy między kluczowymi akcjami (spowolnienie klików)

# Nazwy plików
OUTPUT_FILENAME = "harmonogram.txt"


# --- SŁOWNIKI DO PRZETWARZANIA DANYCH ---

# Słownik mapujący znaki (ikony) z PDF na rodzaj odpadów.
# UWAGA: Te znaki zostały ustalone na podstawie analizy PDF-a i mogą się zmienić.
# Jeśli w przyszłości skrypt przestanie działać, to jest pierwsze miejsce do sprawdzenia.

def download_schedule_pdf():
    """
    Automatyzuje proces wejścia na stronę, wpisania adresu i pobrania pliku PDF.
    """
    print("Krok 1: Pobieranie harmonogramu w formacie PDF...")
    def slow_sleep(label=""):
        if label:
            print(f"   (pauza {ACTION_DELAY}s: {label})")
        time.sleep(ACTION_DELAY)

    def accept_cookies(driver, wait):
        if not CLICK_COOKIES:
            return
        try:
            try:
                consent_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Zgoda na wszystkie']]|//span[text()='Zgoda na wszystkie']/ancestor::button"))
                )
                consent_btn.click()
                print(" - Kliknięto 'Zgoda na wszystkie'")
                slow_sleep("po zgodzie cookies")
                return
            except Exception:
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
                        except Exception:
                            driver.execute_script("arguments[0].click();", buttons[0])
                            print(" - Kliknięto przycisk cookies (JS)")
                        slow_sleep("po cookies")
                        break
        except Exception as e:
            print(f" - Nie kliknięto cookies (ignoruję): {e}")

    def enter_address(driver, wait):
        address_input = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", address_input)
        try:
            address_input.clear()
            address_input.send_keys(ADDRESS_TO_SEARCH)
        except ElementNotInteractableException:
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
                address_input,
                ADDRESS_TO_SEARCH
            )
        print(f" - Wpisano adres: {ADDRESS_TO_SEARCH}")
        slow_sleep("po wpisaniu adresu")
        try:
            WebDriverWait(driver, 10).until(lambda d: (address_input.get_attribute("value") or "").strip().upper().startswith(ADDRESS_TO_SEARCH.split()[0]))
        except Exception:
            pass
        return address_input

    def choose_suggestion(driver, wait, address_input):
        suggestions = wait.until(EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "li.yui3-aclist-item")))
        slow_sleep("lista sugestii załadowana")
        chosen = None
        target_upper = ADDRESS_TO_SEARCH.upper()
        for s in suggestions:
            text_upper = s.text.strip().upper()
            data_text = (s.get_attribute("data-text") or "").strip().upper()
            if text_upper.startswith(target_upper) or data_text.startswith(target_upper):
                chosen = s
                break
        if not chosen:
            chosen = suggestions[0]
        try:
            chosen.click()
        except Exception:
            driver.execute_script("arguments[0].click();", chosen)
        print(" - Wybrano sugestię adresu")
        time.sleep(1)  # Czekaj 1 sekundę po wyborze

    def go_next(driver, wait):
        next_button = wait.until(EC.element_to_be_clickable((By.ID, "buttonNext")))
        next_button.click()
        print(" - Kliknięto 'Dalej'")
        slow_sleep("po 'Dalej'")

    def scrape_schedule_from_page(driver, wait):
        """Czyta daty wywozu bezpośrednio z HTML strony."""
        print(" - Czytam daty z HTML...")
        slow_sleep("ładowanie strony z datami")
        
        # Mapowanie ID divów na nazwy frakcji
        date_divs = {
            "paper-date": "Papier",
            "mixed-date": "Zmieszane",
            "metals-date": "Metale i tworzywa sztuczne",
            "glass-date": "Szkło",
            "bio-date": "Bio"
        }
        
        schedule_data = []
        for div_id, waste_type in date_divs.items():
            try:
                date_element = driver.find_element(By.ID, div_id)
                date_text = date_element.text.strip()
                if date_text:
                    schedule_data.append((date_text, waste_type))
                    print(f"   {waste_type}: {date_text}")
            except Exception as e:
                print(f"   Brak daty dla {waste_type} (ID: {div_id})")
        
        return schedule_data

    # Konfiguracja opcji przeglądarki Chrome
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")

    # Automatyczne zarządzanie sterownikiem przeglądarki
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 25)  # Czekaj maksymalnie 25 sekund na elementy
        accept_cookies(driver, wait)
        address_input = enter_address(driver, wait)
        choose_suggestion(driver, wait, address_input)
        go_next(driver, wait)
        schedule_data = scrape_schedule_from_page(driver, wait)
        return schedule_data
    finally:
        driver.quit()  # Zawsze zamykaj przeglądarkę



def save_schedule_to_file(schedule_data):
    """
    Zapisuje zebrane dane do pliku tekstowego.
    schedule_data: lista tupli (date_text, waste_type) np. [("4 Grudnia", "Papier"), ...]
    """
    print("\nKrok 2: Zapisywanie harmonogramu do pliku...")

    if not schedule_data:
        print(" - Nie znaleziono żadnych terminów do zapisania.")
        return

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"{ADDRESS_TO_SEARCH}\n\n")
        for date_text, waste_type in schedule_data:
            line = f"{date_text} - {waste_type}\n"
            f.write(line)
    
    print(f" - Harmonogram został zapisany w pliku: {OUTPUT_FILENAME}")


def main():
    """
    Główna funkcja sterująca całym procesem.
    """
    print("--- Start: Automat do pobierania harmonogramu wywozu odpadów ---")

    # Usuń stary plik TXT, jeśli istnieje, aby zacząć na czysto
    if os.path.exists(OUTPUT_FILENAME):
        os.remove(OUTPUT_FILENAME)
        
    try:
        # Krok 1: Pobieranie i przetwarzanie
        schedule_data = download_schedule_pdf()
        
        # Krok 2: Zapis
        if schedule_data:
            save_schedule_to_file(schedule_data)
        else:
            print(" - Nie udało się pobrać danych harmonogramu.")

    except Exception as e:
        print(f"\n[BŁĄD] Wystąpił nieoczekiwany problem: {e}")
        print("Sprawdź swoje połączenie z internetem lub czy strona 19115 nie uległa zmianie.")

    print("\n--- Zakończono pracę ---")


if __name__ == "__main__":
    main()
