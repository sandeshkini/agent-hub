# owui-opencode — OpenAI-compatible adapter over `opencode serve` (stdlib only)
FROM python:3.11-slim

WORKDIR /app
COPY opencode_adapter.py ./

ENV PORT=9213 PYTHONUNBUFFERED=1
EXPOSE 9213
CMD ["python3", "opencode_adapter.py"]
