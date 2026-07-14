FROM mcr.microsoft.com/playwright/python:v1.60.0-noble
ARG SERVICE_PATH
WORKDIR /app
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY packages/python ./packages/python
COPY ${SERVICE_PATH} ${SERVICE_PATH}
ENV PYTHONUNBUFFERED=1
