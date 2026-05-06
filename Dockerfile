# Single-image NKN-Monitor: coordinator + VictoriaMetrics + Grafana
# under s6-overlay. För Unraid och liknande deployment där man vill
# ha "en container att hantera". Persistent state under /data.
#
# För dev: använd docker-compose.yml som har separata containers.

ARG GRAFANA_VERSION=11.5.2
ARG VM_VERSION=v1.107.0
ARG S6_OVERLAY_VERSION=3.2.0.2

# --- Grafana som källa för binärer + plugins ---------------------------------
FROM grafana/grafana:${GRAFANA_VERSION} AS grafana-src

# --- VictoriaMetrics binär ---------------------------------------------------
FROM victoriametrics/victoria-metrics:${VM_VERSION} AS vm-src

# --- Slutbild ----------------------------------------------------------------
FROM python:3.12-slim AS final

ARG S6_OVERLAY_VERSION
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    GF_PATHS_DATA=/data/grafana \
    GF_PATHS_LOGS=/data/grafana/logs \
    GF_PATHS_PLUGINS=/data/grafana/plugins \
    GF_PATHS_PROVISIONING=/etc/grafana/provisioning \
    GF_PATHS_HOME=/usr/share/grafana \
    DATABASE_PATH=/data/coordinator.db \
    COORDINATOR_CONFIG=/data/config.yaml \
    VICTORIAMETRICS_URL=http://127.0.0.1:8428 \
    LOG_LEVEL=INFO

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils tini libfontconfig1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r -g 472 grafana \
    && useradd -r -u 472 -g grafana -d /usr/share/grafana -s /sbin/nologin grafana

# s6-overlay (multi-arch via TARGETARCH)
RUN ARCH="$(uname -m)" \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
       | tar -C / -Jxpf - \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${ARCH}.tar.xz" \
       | tar -C / -Jxpf -

# Grafana
COPY --from=grafana-src /usr/share/grafana /usr/share/grafana
COPY --from=grafana-src /etc/grafana /etc/grafana
RUN ln -s /usr/share/grafana/bin/grafana /usr/local/bin/grafana

# Förinstallerade plugins via grafana-cli
RUN /usr/share/grafana/bin/grafana cli --homepath /usr/share/grafana --pluginsDir /usr/share/grafana/plugins-bundled \
    plugins install yesoreyeram-infinity-datasource

# VictoriaMetrics binär
COPY --from=vm-src /victoria-metrics-prod /usr/local/bin/victoria-metrics

# Coordinator
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY coordinator/pyproject.toml /app/coordinator/pyproject.toml
RUN uv pip install --system --no-cache \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.27" \
      "pydantic>=2.9" "pyyaml>=6.0"
COPY coordinator/src /app/coordinator/src

# Default-config (kopieras till /data/config.yaml vid första start om saknas)
COPY coordinator/config.yaml /usr/share/nkn-mon/config.yaml.default

# PowerShell-klient som distribueras till probes via /probe/client/download
COPY client/NknMonitor.ps1 /app/client/NknMonitor.ps1

# Grafana-provisioning (read-only) och defaultdashboards
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /usr/share/grafana/dashboards-default

# s6-overlay services + init
COPY docker/s6-overlay /etc

EXPOSE 8200 3000

VOLUME ["/data"]

ENTRYPOINT ["/init"]
