FROM python:3.11-slim

WORKDIR /app

# openssh-client : diagnostic serveur optionnel (clé SSH limitée à un script côté serveur)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /logs

EXPOSE 10911

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10911"]
