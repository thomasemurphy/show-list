FROM python:3.12-slim

WORKDIR /app

COPY webhook/requirements.txt webhook/requirements.txt
RUN pip install --no-cache-dir -r webhook/requirements.txt

COPY shared/ shared/
COPY webhook/ webhook/

ENV PYTHONPATH=/app
ENV PORT=8080

# Shell form so $PORT (injected by Cloud Run) is expanded at runtime.
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 30 webhook.main:app
