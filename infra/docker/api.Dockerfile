# FastAPI REST + SSE server
#
# Why python:3.11-slim and not python:3.14:
#   asyncpg ships pre-built wheels for 3.11. 3.14 is too new for binary wheels,
#   which means it would compile from source inside Docker — adds 5+ minutes to
#   build time and requires gcc/build-essential in the image. 3.11 is the current
#   LTS target for edge deployment.
#
# Why copy the full services/ directory (not just services/api/):
#   services/api/ imports nothing from sibling packages today, but the package
#   layout (services/api/main.py → services.api.main) requires services/ to exist
#   as a Python package in the WORKDIR. Copying just services/api/ would break
#   the uvicorn module path.

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first — Docker layer cache: this layer only rebuilds when
# requirements.txt changes, not on every source code edit.
COPY services/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source — separate layer from deps for cache efficiency
COPY services/ ./services/

EXPOSE 8080

# uvicorn: 1 worker in dev (--reload watches for changes)
# docker-compose.edge.yml overrides CMD to remove --reload and set workers > 1
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
