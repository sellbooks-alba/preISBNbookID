FROM python:3.12-slim

WORKDIR /app

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# Render (and most PaaS hosts) inject $PORT at runtime — must bind to it,
# not a hardcoded port. 5000 is just the local-dev fallback.
ENV PORT=5000
EXPOSE 5000

# --timeout 120: a full search does 4 sequential source lookups plus a
# network fetch + decode per candidate image — legitimately slow on a free
# tier's shared CPU, well past gunicorn's 30s default even after fixing the
# redundant reference-decode bug (see match.load_reference).
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 app:app
