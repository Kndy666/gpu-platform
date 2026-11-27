ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.base \
    PLATFORM_DATA=/var/lib/gpu-platform \
    PLATFORM_SKILL_DIRECTORY=/opt/gpu-platform/skills/gpu-platform \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH
WORKDIR /opt/gpu-platform
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home platform \
    && install -d -o 10001 -g 10001 /var/lib/gpu-platform
COPY packages/ packages/
COPY modules/ modules/
COPY pyproject.toml uv.lock ./
COPY skills/ skills/
RUN pip install --no-cache-dir uv==0.13.8 \
    && uv sync --locked --no-dev --no-editable \
      --package gpu-platform-control --package gpu-platform-panel \
      --package gpu-platform-auth-local --package gpu-platform-transport-local \
      --package gpu-platform-transport-reverse-ssh --package gpu-platform-entry-local \
      --package gpu-platform-entry-controller-forward --package gpu-platform-notify-pushplus \
      --package gpu-platform-notify-inapp \
    && rm -rf /root/.cache/uv
RUN DJANGO_DEBUG=1 python -m gpu_platform_control.manage collectstatic --noinput
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "gpu_platform_control.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
