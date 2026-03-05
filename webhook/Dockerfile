FROM python:3.12-slim

WORKDIR /app

COPY webhook/requirements.txt webhook/requirements.txt
RUN pip install --no-cache-dir -r webhook/requirements.txt

COPY shared/ shared/
COPY webhook/ webhook/

ENV PYTHONPATH=/app
ENV PORT=5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "30", "webhook.main:app"]
