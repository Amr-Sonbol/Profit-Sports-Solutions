FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# gettext for translations; Pango, HarfBuzz and the Noto fonts (which
# include Arabic) for WeasyPrint, which draws the visit report PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=100 -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
