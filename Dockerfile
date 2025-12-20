# Baza: lekki Linux z Pythonem
FROM python:3.11-slim

# Ustawienia, żeby logi widoczne były od razu
ENV PYTHONUNBUFFERED=1

# Instalacja Chromium (przeglądarki), sterowników i czcionek
# fonts-liberation jest KLUCZOWE, żebyś mógł pisać polskie znaki na PDF w Dockerze
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    wget \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Folder roboczy w kontenerze
WORKDIR /app

# Najpierw instalujemy biblioteki (dzięki temu kolejne budowania będą szybsze)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy resztę Twoich plików
COPY . .

# Otwieramy port
EXPOSE 5000

# Startujemy aplikację
CMD ["python", "app.py"]