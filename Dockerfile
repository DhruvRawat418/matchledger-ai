FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MATCHLEDGER_DATA_DIR=/app/outputs
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY src ./src
COPY procurement_recon ./procurement_recon
COPY demo ./demo
COPY eval ./eval
RUN mkdir -p /app/outputs && chown appuser:appuser /app/outputs
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=3)"
CMD ["python", "-m", "uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
COPY docs ./docs
COPY pytest.ini make_sample_cases.py ./
USER appuser
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
