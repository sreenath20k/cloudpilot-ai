# CloudPilot AI - container image for the web service that runs on AWS ECS Fargate.

# 1. Base image: official slim Python (Debian-based, small, has what we need).
FROM python:3.13-slim

# 2. Python settings: no .pyc files, unbuffered logs (so they appear in CloudWatch immediately).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 3. All app files live in /app inside the container.
WORKDIR /app

# 4. Copy ONLY requirements first and install them. Docker caches this layer, so
#    rebuilding after a code change skips the slow pip install step.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 5. Create an unprivileged user so the app does not run as root.
RUN useradd --create-home --uid 10001 appuser

# 6. Copy the application code (files listed in .dockerignore are skipped).
COPY --chown=appuser:appuser . .
USER appuser

# 7. Document the port the app listens on (the ECS task definition maps it).
EXPOSE 8000

# 8. Docker-level health check. Uses Python's standard library because the slim
#    image has no curl. Marks the container "unhealthy" if /health stops returning 200.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# 9. Start the server on 0.0.0.0 so it is reachable from outside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
