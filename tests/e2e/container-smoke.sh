#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
image="${PLATFORM_TEST_IMAGE:-gpu-platform:0.3.0}"
name="gpu-platform-smoke-$$"
volume="${name}-data"
smoke_dir="$(mktemp -d -t gpu-platform-smoke.XXXXXX)"
chmod 700 "${smoke_dir}"
cleanup() { docker rm -f "${name}" >/dev/null 2>&1 || true; docker volume rm "${volume}" >/dev/null 2>&1 || true; rm -rf "${smoke_dir}"; }
trap cleanup EXIT
python3 - "${smoke_dir}/app.env" <<'PY'
import secrets,sys
from pathlib import Path
p=Path(sys.argv[1]);p.write_text('DJANGO_SECRET_KEY='+secrets.token_urlsafe(48)+'\nPLATFORM_HTTPS=0\n');p.chmod(0o600)
PY
docker volume create "${volume}" >/dev/null
docker run --rm --user 0 -v "${volume}:/var/lib/platform" --entrypoint chown "${image}" 10001:10001 /var/lib/platform
start() {
  docker run -d --name "${name}" -p 127.0.0.1:18080:8000 --env-file "${smoke_dir}/app.env" \
    -e PLATFORM_CONFIG=/etc/platform/config.yaml -v "${project_root}/config.example.yaml:/etc/platform/config.yaml:ro" \
    -v "${volume}:/var/lib/platform" "${image}" >/dev/null
  for attempt in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:18080/accounts/login/ > "${smoke_dir}/login.html"; then return; fi
    sleep 1
  done
  docker logs --tail=40 "${name}"; exit 1
}
start
curl -fsS http://127.0.0.1:18080/static/portal/style.css > "${smoke_dir}/style.css"
docker exec "${name}" python manage.py shell -c 'from portal.models import AuditLog; AuditLog.objects.create(action="container_smoke",target="upgrade-persistence")' >/dev/null
docker stop "${name}" >/dev/null
docker rm "${name}" >/dev/null
start
docker exec "${name}" python manage.py shell -c 'from portal.models import AuditLog; assert AuditLog.objects.filter(action="container_smoke",target="upgrade-persistence").count()==1; print("History persists after container replacement")'
echo 'Container schema initialization, login, static assets, replacement persistence: PASS'
