# Immutable stable upstream runtime, including PM-pinned Python 3.14, tools,
# dependencies and compiled dashboard/TUI. The existing app wrapper remains
# the only gateway supervisor; the upstream s6 entrypoint is not started.
FROM nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

USER root
ARG HERMES_REF=v0.22.5
ARG HERMES_COMMIT=bf0203a794a07aac3ff764972ab9087baa4e4f4e
ENV HERMES_REF=${HERMES_REF}

# Fail the build if the resolved image is not the reviewed release. Read the
# upstream provenance instead of trusting the display version environment.
RUN /opt/hermes/.venv/bin/python -c 'import json, os, pathlib, sys; stamp = json.loads(pathlib.Path("/opt/hermes/install-stamp.json").read_text()); assert stamp["commit"] == os.environ["HERMES_COMMIT"], stamp.get("commit"); assert sys.version_info[:2] == (3, 14), sys.version'

RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/* && \
    ln -s /opt/hermes /opt/hermes-agent && \
    mkdir -p /data/.hermes

# Bypass the upstream root-to-hermes CLI shim, which changes HOME to /opt/data.
# This wrapper already owns its subprocesses and keeps its existing /data home.
# The CLI here is the upstream assembler's executable, not a pip reinstall.
ENV PATH="/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV PYTHONPATH=/opt/hermes
ENV HOME=/data
ENV HERMES_HOME=/data/.hermes
# The existing wrapper and persistent volume intentionally use root. Its tini
# entrypoint replaces upstream's privilege drop, so explicitly opt in through
# the supported gateway guard without changing users or volume ownership.
ENV HERMES_ALLOW_ROOT_GATEWAY=1
ENV HERMES_WRITE_SAFE_ROOT=/data
ENV HERMES_TUI_DIR=/opt/hermes/ui-tui
ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist

COPY server.py /app/server.py
COPY templates/ /app/templates/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh && \
    python -c 'import starlette, uvicorn, jinja2, multipart, httpx, websockets, hermes_yaml; import ast; ast.parse(open("/app/server.py").read())'

# Do not run the upstream s6 services as well as the app's managed gateway.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/app/start.sh"]
