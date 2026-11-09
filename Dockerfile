ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLATFORM_DATA=/var/lib/platform
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home platform \
    && mkdir -p /var/lib/platform && chown platform:platform /var/lib/platform
COPY backend/ ./
COPY images/runner.sh ./portal/workspace_runner.sh
COPY images/Dockerfile ./portal/workspace.Dockerfile
COPY images/docker.sh ./portal/workspace_docker.sh
COPY images/vendor/ ./portal/workspace_assets/
COPY skills/gpu-platform/ ./agent-skill/gpu-platform/
RUN DJANGO_DEBUG=1 python manage.py collectstatic --noinput
USER 10001:10001
EXPOSE 8000
CMD ["python", "supervise.py"]
