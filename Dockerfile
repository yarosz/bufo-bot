FROM python:3.12-slim

WORKDIR /opt/bufo

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bufo-descriptions.json* ./
COPY bufo-manifest.json* ./
COPY scripts/ scripts/

CMD ["python", "scripts/bufo-discovery-bot.py"]
