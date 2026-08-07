# Tiny image for the node registrar (heartbeats this machine into the OWUI machine selector).
# Stdlib-only Python — no deps.
FROM python:3.12-slim
WORKDIR /app
COPY registrar.py .
CMD ["python", "-u", "registrar.py"]
