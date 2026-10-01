FROM python:3.13-bookworm
WORKDIR /workspace
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps .
EXPOSE 8000
CMD ["python", "-m", "app.server"]
