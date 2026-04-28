FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY deep_research ./deep_research

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

CMD ["deep-research", "serve"]
