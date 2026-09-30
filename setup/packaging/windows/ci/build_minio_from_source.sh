#!/usr/bin/env bash
# CI UNIQUEMENT : l'image minio/minio n'est plus distribuée publiquement (Docker Hub « repository does not
# exist », quay.io « unauthorized » — constaté le 30/09/2026). Pour tester le profil Compose facultatif
# « minio », l'image est construite depuis les sources OFFICIELLES (github.com/minio/minio et
# github.com/minio/mc, AGPL) sous le nom attendu par docker-compose.yml. Jamais exécuté chez l'utilisateur.
set -euo pipefail
if docker image inspect minio/minio:latest >/dev/null 2>&1 || docker pull -q minio/minio:latest >/dev/null 2>&1; then
  echo "[build_minio_from_source] image minio/minio:latest déjà disponible"
  exit 0
fi
build=$(mktemp -d)
cat > "$build/Dockerfile" <<'DOCKERFILE'
FROM golang:1.24 AS build
RUN CGO_ENABLED=0 go install github.com/minio/minio@latest && CGO_ENABLED=0 go install github.com/minio/mc@latest
FROM debian:bookworm-slim
COPY --from=build /go/bin/minio /go/bin/mc /usr/bin/
ENTRYPOINT ["minio"]
DOCKERFILE
docker build -q -t minio/minio:latest "$build" >/dev/null
echo "[build_minio_from_source] $(docker run --rm minio/minio:latest --version 2>&1 | head -1) ; mc : $(docker run --rm --entrypoint mc minio/minio:latest --version 2>&1 | head -1)"
