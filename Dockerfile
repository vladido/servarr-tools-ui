FROM python:3.13-slim AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Servarr Tools UI" \
      org.opencontainers.image.revision="$VCS_REF"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LISTEN_PORT=8080
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --chown=10001:10001 servarr_ui ./servarr_ui
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD ["python", "-c", "import os,urllib.request; allowed=os.environ.get('SERVARR_TOOLS_ALLOWED_HOSTS','localhost:8787,127.0.0.1:8787').split(',',1)[0].strip(); request=urllib.request.Request('http://127.0.0.1:'+os.environ.get('LISTEN_PORT','8080')+'/healthz',headers={'Host':allowed}); urllib.request.urlopen(request,timeout=2).read()"]
ENTRYPOINT ["python", "-m", "servarr_ui.server"]
