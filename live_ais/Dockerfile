FROM python:3.11-slim
WORKDIR /app
COPY live_ais/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home collector
COPY live_ais/collector.py live_ais/bboxes.json /app/
USER collector
CMD ["python", "-u", "/app/collector.py"]
