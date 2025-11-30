# Używamy nowszego, stabilnego Pythona 3.11
FROM python:3.11-slim

# Instalacja Chromium i sterowników
# Te komendy są kluczowe, bo Selenium potrzebuje przeglądarki systemowej
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Ustawienie zmiennych, żeby Python nie tworzył plików .pyc i buforował wyjścia
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Najpierw kopiujemy tylko requirements, żeby Docker cache'ował instalację bibliotek
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dopiero potem kopiujemy resztę kodu
COPY . .

# Uruchomienie
CMD ["python", "app.py"]