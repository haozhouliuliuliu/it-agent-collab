FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY seed_data ./seed_data
RUN pip install .

RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app
USER agent

ENV COLLAB_DB_PATH=/app/data/collab.db \
    COLLAB_SEED_DIR=/app/seed_data

ENTRYPOINT ["team-agent"]
CMD ["--help"]
