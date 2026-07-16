FROM python:3.12-slim

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# Render (and most PaaS hosts) inject $PORT at runtime — must bind to it,
# not a hardcoded port. 5000 is just the local-dev fallback.
ENV PORT=5000
EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 app:app
