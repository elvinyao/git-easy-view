# BuildKit is required. No external Dockerfile frontend image is needed.
FROM python:3.13-bookworm
WORKDIR /workspace
COPY deploy/trust.py deploy/install.py deploy/entrypoint.py /opt/gev-deploy/
COPY requirements.lock pyproject.toml ./
# CA rotation must invalidate dependency layers; secret values stay out of ARG/ENV.
ARG GEV_CA_FINGERPRINT
RUN --mount=type=secret,id=build_network,required=true \
    --mount=type=secret,id=corporate_ca,required=true \
    test -n "$GEV_CA_FINGERPRINT" && python /opt/gev-deploy/install.py -r requirements.lock
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=secret,id=build_network,required=true \
    --mount=type=secret,id=corporate_ca,required=true \
    python /opt/gev-deploy/install.py --no-deps .
EXPOSE 8000
ENTRYPOINT ["python", "/opt/gev-deploy/entrypoint.py"]
CMD ["python", "-m", "app.server"]
