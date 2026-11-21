FROM ubuntu:24.04
# nmap comes with the image, so the laptop does not need it installed.
RUN apt-get update && apt-get install -y --no-install-recommends nmap python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/aegis
ENV PATH=/opt/aegis/bin:$PATH
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml .
COPY aegis ./aegis
RUN pip install --no-cache-dir --no-deps .
ENV AEGIS_DATA=/data AEGIS_KEY_FILE=/keys/aegis.key AEGIS_HOST=0.0.0.0 AEGIS_PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["aegis", "serve"]
