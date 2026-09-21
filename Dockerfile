FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 agora && chown -R agora:agora /srv

# Numerisch, nicht "USER agora": steht hier ein Name, kann kubelet ihn nicht
# aufloesen und lehnt den Start mit runAsNonRoot: true ab -
# CreateContainerConfigError, ohne brauchbare Meldung im Pod. Mit der Nummer
# sieht kubelet direkt, dass es nicht root ist.
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
