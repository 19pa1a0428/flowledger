FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/flowledger/bin:$PATH

WORKDIR /opt/flowledger
RUN addgroup --system flowledger && adduser --system --ingroup flowledger flowledger

COPY pyproject.toml README.md alembic.ini ./
COPY flowledger ./flowledger
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir .

USER flowledger
EXPOSE 8000
CMD ["uvicorn", "flowledger.api:app", "--host", "0.0.0.0", "--port", "8000"]
