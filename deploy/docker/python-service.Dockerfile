FROM python:3.11-slim
ARG SERVICE_PATH
WORKDIR /app
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY packages/python ./packages/python
COPY ${SERVICE_PATH} ${SERVICE_PATH}
ENV PYTHONUNBUFFERED=1
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
