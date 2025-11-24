import os
import time

# Selenium - do automatyzacji przeglądarki
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementNotInteractableException
from webdriver_manager.chrome import ChromeDriverManager

# --- KONFIGURACJA ---
ADDRESS_TO_SEARCH = "Obozowa 90"
TARGET_URL = "https://warszawa19115.pl/harmonogramy-wywozu-odpadow"

# Flagi działania
HEADLESS = False       # True = brak okna przeglądarki
CLICK_COOKIES = True   # True = próba zamknięcia baneru cookies
ACTION_DELAY = 0.1     # Opóźnienie dla "ludzkości" interakcji

# Nazwa pliku wyjściowego
OUTPUT_FILENAME = "harmonogram.txt"

def fetch_waste_schedule():
    """
    Uruchamia przeglądarkę, wpisuje adres i pobiera daty wywozu bezpośrednio z HTML.
    """
    print("Krok 1: Pobieranie danych ze strony WWW...")

    def slow_sleep(label=""):
        if label:
            # Opcjonalnie: print(f"   (pauza: {label})")
            pass
        time.sleep(ACTION_DELAY)

    def accept_cookies(driver, wait):
        if not CLICK_COOKIES:
            return
        try:
            # Najpierw próba standardowego przycisku
            try:
                consent_btn = WebDriverWait(driver, 4).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Zgoda na wszystkie']]|//span[text()='Zgoda na wszystkie']/ancestor::button"))
                )
                consent_btn.click()
                slow_sleep("po zgodzie cookies")
                return
            except Exception:
                pass

        except Exception as e:
            print(f" - Nie kliknięto cookies (ignoruję): {e}")

    def enter_address(driver, wait):
        address_input = wait.until(EC.element_to_be_clickable((By.ID, "addressAutoComplete")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", address_input)
        try:
            address_input.clear()
            address_input.send_keys(ADDRESS_TO_SEARCH)
        except ElementNotInteractableException:
            # Fallback JS jeśli element jest zasłonięty
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
                address_input,
                ADDRESS_TO_SEARCH
            )
        print(f" - Wpisano adres: {ADDRESS_TO_SEARCH}")
        slow_sleep("po wpisaniu adresu")
        return address_input

    def choose_suggestion(driver, wait):
        suggestions = wait.until(EC.visibility_of_all_elements_located((By.CSS_SELECTOR, "li.yui3-aclist-item")))
        
        # Prosta logika wyboru najlepszego dopasowania
        chosen = None
        target_upper = ADDRESS_TO_SEARCH.upper()
        for s in suggestions:
            text_upper = s.text.strip().upper()
            data_text = (s.get_attribute("data-text") or "").strip().upper()
            if text_upper.startswith(target_upper) or data_text.startswith(target_upper):
                chosen = s
                break
        
        if not chosen:
            chosen = suggestions[0] # Bierzemy pierwszy jeśli brak idealnego dopasowania

        try:
            chosen.click()
        except Exception:
            driver.execute_script("arguments[0].click();", chosen)
        print(" - Wybrano sugestię z listy")

    def go_next(driver, wait):
        next_button = wait.until(EC.element_to_be_clickable((By.ID, "buttonNext")))
        next_button.click()
        print(" - Kliknięto 'Dalej'")
        slow_sleep("po 'Dalej'")

    def scrape_html_data(driver):
        """Czyta daty wywozu bezpośrednio z elementów HTML."""
        print(" - Analiza wyników na stronie...")
        
        # Identyfikatory elementów na stronie 19115
        date_divs = {
            "paper-date": "Papier",
            "mixed-date": "Zmieszane",
            "metals-date": "Metale i tworzywa sztuczne",
            "glass-date": "Szkło",
            "bio-date": "Bio",
            # "bulky-date": "Gabaryty", # Opcjonalnie, jeśli istnieje
            # "green-date": "Zielone"   # Opcjonalnie
        }
        
        data = []
        for div_id, waste_type in date_divs.items():
            try:
                # Szukamy elementu po ID
                element = driver.find_element(By.ID, div_id)
                text_val = element.text.strip()
                if text_val:
                    data.append((text_val, waste_type))
                    print(f"   -> Znaleziono: {waste_type} ({text_val})")
            except Exception:
                # Element może nie istnieć dla danego adresu/czasu
                pass
        
        return data

    # --- Start sterownika Chrome ---
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    # Tłumienie logów Selenium w konsoli
    chrome_options.add_argument("--log-level=3")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        driver.get(TARGET_URL)
        wait = WebDriverWait(driver, 25)

        accept_cookies(driver, wait)
        enter_address(driver, wait)
        choose_suggestion(driver, wait)
        go_next(driver, wait)
        
        # Poczekaj chwilę na przeładowanie treści po kliknięciu Dalej
        time.sleep(1.5) 
        
        return scrape_html_data(driver)

    except Exception as e:
        print(f"[BŁĄD Selenium] {e}")
        return []
    finally:
        driver.quit()


def save_schedule_to_file(schedule_data):
    """
    Zapisuje zebrane dane do pliku tekstowego.
    """
    print("\nKrok 2: Zapis do pliku...")

    if not schedule_data:
        print(" - Pusta lista danych, nic nie zapisano.")
        return

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"Harmonogram dla: {ADDRESS_TO_SEARCH}\n")
        f.write(f"Wygenerowano: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write("-" * 40 + "\n")
        
        for date_text, waste_type in schedule_data:
            f.write(f"{date_text} : {waste_type}\n")
    
    print(f" - Sukces! Dane w pliku: {OUTPUT_FILENAME}")


def main():
    print("--- Start: Automat Warszawa 19115 (HTML Scraper) ---")

    # Sprzątanie starego pliku
    if os.path.exists(OUTPUT_FILENAME):
        os.remove(OUTPUT_FILENAME)
        
    schedule = fetch_waste_schedule()
    
    if schedule:
        save_schedule_to_file(schedule)
    else:
        print(" - Nie udało się pobrać harmonogramu.")

    print("\n--- Koniec ---")


if __name__ == "__main__":
    main()