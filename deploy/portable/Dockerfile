FROM python:3.14-slim-trixie

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/researcher CBMC_BIN=/usr/bin TRACE_ZIG_EXECUTABLE=/opt/zig/zig \
    Z3_EXECUTABLE=/usr/bin/z3
WORKDIR /app

# These are the same CBMC and Zig release families used by the native adapters.
# Official download checksums: CBMC GitHub release assets; ziglang.org download index.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils gcc libc6-dev z3 && \
    curl --fail --location --retry 3 \
        https://github.com/diffblue/cbmc/releases/download/cbmc-6.11.0/ubuntu-24.04-cbmc-6.11.0-Linux.deb \
        -o /tmp/cbmc.deb && \
    echo 'b3721aa541038384d7801ea3aeabbcddc3e8845ac8f1cbff637cf8dec7481ac8  /tmp/cbmc.deb' | sha256sum -c - && \
    apt-get install -y --no-install-recommends /tmp/cbmc.deb && \
    curl --fail --location --retry 3 \
        https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz \
        -o /tmp/zig.tar.xz && \
    echo 'd45312e61ebcc48032b77bc4cf7fd6915c11fa16e4aad116b66c9468211230ea  /tmp/zig.tar.xz' | sha256sum -c - && \
    mkdir /opt/zig && tar -xJf /tmp/zig.tar.xz --strip-components=1 -C /opt/zig && \
    rm /tmp/cbmc.deb /tmp/zig.tar.xz && rm -rf /var/lib/apt/lists/* && \
    useradd --uid 10001 --no-create-home researcher && \
    mkdir /app/results && chown researcher:researcher /app/results

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY backend ./backend
COPY presentation ./presentation
COPY examples ./examples
COPY scripts ./scripts
COPY tests ./tests
COPY benchmarks/source_manifest.json ./benchmarks/source_manifest.json
COPY benchmarks/source_verified_v1 ./benchmarks/source_verified_v1
COPY bootstrap.py app.py ./
COPY deploy/portable/smoke_check.py ./deploy/portable/smoke_check.py
RUN python deploy/portable/smoke_check.py --build-check

USER researcher
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"]
