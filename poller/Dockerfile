FROM python:3.12-slim

WORKDIR /app

COPY poller/requirements.txt poller/requirements.txt
RUN pip install --no-cache-dir -r poller/requirements.txt

COPY shared/ shared/
COPY poller/ poller/

ENV PYTHONPATH=/app

CMD ["python", "-m", "poller.main"]
