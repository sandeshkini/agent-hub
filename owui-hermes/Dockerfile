# owui-hermes — streaming OpenAI-compatible adapter over host Hermes /api/ws
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY hermes_adapter.py ./

ENV PORT=9211 PYTHONUNBUFFERED=1
EXPOSE 9211
CMD ["python3", "hermes_adapter.py"]
