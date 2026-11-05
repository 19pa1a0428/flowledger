FROM python:3.12-slim AS runtime

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
