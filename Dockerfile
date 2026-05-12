FROM python:3.12-slim

LABEL org.opencontainers.image.title="CodeGuardian"
LABEL org.opencontainers.image.description="AI code review tool with 10 parallel agents"
LABEL org.opencontainers.image.licenses="BSL-1.1"

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY --chown=appuser:appuser . .
RUN pip install -e . --no-deps

USER appuser

ENTRYPOINT ["codeguardian"]
CMD ["--help"]
