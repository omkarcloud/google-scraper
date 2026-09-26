FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# Camoufox (a hardened Firefox) opens Google Search sessions; it needs Firefox's
# system libraries and a virtual display (Xvfb) inside a container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb libgtk-3-0 libdbus-glib-1-2 libxt6 libx11-xcb1 libasound2 libpci3 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt && python -m camoufox fetch

RUN mkdir app
WORKDIR /app
COPY . /app

EXPOSE 8000

CMD ["python", "run.py"]
