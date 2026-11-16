# Lifetime Server as a container image: the "runs on every Linux" option (Docker or Podman, any distribution,
# including musl ones such as Alpine as the host). Headless; no Qt. Data lives in the volume /var/lib/lifetime-server.
#
#   docker build -t lifetime-server .
#   docker run -d --name lifetime-server --restart unless-stopped -p 8443:8443 \
#       -e LIFETIME_ADDRESS=https://server.school.lan:8443 -v lifetime-data:/var/lib/lifetime-server lifetime-server
#   docker exec lifetime-server lifetime-admin --operation invitation
#
# The loopback administration port 8444 stays inside the container and is never published.
FROM python:3.12-slim-bookworm

RUN groupadd --system lifetime \
    && groupadd --system lifetime-admin \
    && groupadd --system lifetime-console \
    && useradd --system --gid lifetime --groups lifetime-admin,lifetime-console --home-dir /var/lib/lifetime-server \
        --no-create-home --shell /usr/sbin/nologin lifetime

WORKDIR /opt/lifetime-server
COPY requirements-server.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements-server.txt
COPY lifetime_*.py ./
COPY LICENSE THIRD_PARTY_NOTICES.md ./
RUN install -d -o lifetime -g lifetime -m 0700 /var/lib/lifetime-server \
    && install -d -o lifetime -g lifetime -m 0755 /run/lifetime-server \
    && printf '#!/bin/sh\nexec python /opt/lifetime-server/lifetime_server.py admin --directory /var/lib/lifetime-server --admin-port 8444 "$@"\n' \
        > /usr/local/bin/lifetime-admin \
    && chmod 0755 /usr/local/bin/lifetime-admin \
    && python -m compileall -q /opt/lifetime-server

USER lifetime
ENV LIFETIME_ADDRESS=https://localhost:8443 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
VOLUME ["/var/lib/lifetime-server"]
EXPOSE 8443
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8443), 5).close()"
ENTRYPOINT ["sh", "-c", "exec python /opt/lifetime-server/lifetime_server.py console --directory /var/lib/lifetime-server --address \"$LIFETIME_ADDRESS\" --host 0.0.0.0 --port 8443 --admin-port 8444"]
