FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Instalujemy Chromium i sterownik z TEGO SAMEGO źródła (Debian repo)
# To gwarantuje zgodność wersji (np. browser v120 i driver v120)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Usuń webdriver-manager z requirements jeśli tam jest, 
# bo w Dockerze użyjemy systemowego sterownika
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]