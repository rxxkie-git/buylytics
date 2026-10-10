# FastAPI backend.
#
# Built and run by the root docker-compose.yml:
#
#   docker compose up --build
#
# The database is not in here and not in the compose file. PostgreSQL is Supabase,
# a managed service and the source of truth, so DATABASE_URL is supplied from the
# environment and points outside the Docker network. Redis and Kafka are reached
# by service name — never localhost, which inside a container means this container.
#
# One stage, on purpose. Every runtime dependency ships a manylinux wheel
# (psycopg[binary], numpy, scikit-learn, statsmodels), so nothing is compiled and
# a builder stage would copy site-packages around for no size win.

FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: the image is read-only in practice and .pyc files
# written by a non-root user into a root-owned tree fail noisily for no benefit.
# PYTHONUNBUFFERED: without it, uvicorn's startup lines sit in a pipe buffer and
# `docker compose logs` looks like the process hung.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source. Editing a route then rebuilding reuses this layer
# instead of reinstalling pandas and scikit-learn, which is the difference between
# a five-second and a three-minute rebuild.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY models ./models
COPY alembic ./alembic
COPY alembic.ini ./

# Not root. The process only needs to read its own code and write
# preprocessing_outputs/, which app/main.py creates on startup — hence the chown
# of /app rather than of one subdirectory.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health answers from memory and never touches a dependency, so this reports
# whether the API process is alive rather than whether Supabase is reachable —
# which is what a container healthcheck should mean. Readiness, including the
# database, is /health/ready and is deliberately not used here: a Supabase blip
# should not make Docker restart a perfectly healthy process.
#
# python instead of curl: curl is not in the slim image and installing it to run
# one request is 8 MB and an extra package to keep patched.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# --proxy-headers so the client IP and scheme in logs come from nginx's
# X-Forwarded-* rather than showing every request as arriving from the frontend
# container. --forwarded-allow-ips=* is safe only because nothing outside the
# compose network can reach this port; the published port is nginx's, not this one.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
