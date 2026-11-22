# AI Salesperson: container image.
# OBS, VTube Studio and TikTok LIVE Studio stay on your desktop; the container reaches them through
# host.docker.internal. For screen reading, run screen_agent.bat on the host (see README "Docker").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
# The OCR pulls in desktop OpenCV; swap it for the headless build, which needs no GUI/system libraries.
RUN pip install -r requirements.txt \
 && pip uninstall -y opencv-python \
 && pip install "opencv-python-headless>=4.8"

COPY salesbot ./salesbot
COPY sample_products.csv README.md THIRD_PARTY_NOTICES.md ./

RUN useradd --create-home --uid 10001 salesbot && mkdir -p /data && chown salesbot /data
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

# Check the OCR model loads (it ships inside the package, so no download at runtime).
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

ENV SALESBOT_HOST=0.0.0.0 \
    SALESBOT_PORT=8765 \
    SALESBOT_DATA=/data \
    SALESBOT_NO_BROWSER=1 \
    SALESBOT_DEFAULT_OBS_HOST=host.docker.internal \
    SALESBOT_DEFAULT_VTS_HOST=host.docker.internal \
    SALESBOT_DEFAULT_SCREEN_AGENT_URL=http://host.docker.internal:8766

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8765/api/state', timeout=4); sys.exit(0)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "salesbot"]
