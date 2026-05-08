FROM python:3.12-slim

LABEL org.opencontainers.image.title="CodeGuardian"
LABEL org.opencontainers.image.description="Multi-Agent AI code review system powered by MiMo"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .
RUN pip install -e . --no-deps

ENTRYPOINT ["codeguardian"]
CMD ["--help"]
