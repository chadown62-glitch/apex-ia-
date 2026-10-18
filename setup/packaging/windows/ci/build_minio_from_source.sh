#!/usr/bin/env bash
# CI UNIQUEMENT : l'image minio/minio n'est plus distribuée publiquement (Docker Hub « repository does not
# exist », quay.io « unauthorized » — constaté le 30/09/2026). Pour tester le profil Compose facultatif
# « minio », l'image est construite depuis les sources OFFICIELLES (github.com/minio/minio et
# github.com/minio/mc, AGPL) sous le nom attendu par docker-compose.yml. Jamais exécuté chez l'utilisateur.
#
# Versions figées : ce que `@latest` donnait depuis février 2026 (proxy.golang.org, relevé le 18/10/2026).
# Les caches de Go (2,4 Go mesurés) sont effacés dans la même couche, et l'image de construction est retirée
# ensuite : la distribution WSL vit sur le disque C: du runner, qui s'est rempli pendant cette construction.
set -euo pipefail
MINIO_VERSION=v0.0.0-20260212201848-7aac2a2c5b7c
MC_VERSION=v0.0.0-20251106162529-77f82e18b540
if docker image inspect minio/minio:latest >/dev/null 2>&1 || docker pull -q minio/minio:latest >/dev/null 2>&1; then
  echo "[build_minio_from_source] image minio/minio:latest déjà disponible"
  exit 0
fi
build=$(mktemp -d)
cat > "$build/Dockerfile" <<DOCKERFILE
FROM golang:1.24 AS build
RUN CGO_ENABLED=0 go install -trimpath -ldflags "-s -w" github.com/minio/minio@${MINIO_VERSION} \\
 && CGO_ENABLED=0 go install -trimpath -ldflags "-s -w" github.com/minio/mc@${MC_VERSION} \\
 && go clean -cache -modcache
FROM debian:bookworm-slim
COPY --from=build /go/bin/minio /go/bin/mc /usr/bin/
ENTRYPOINT ["minio"]
DOCKERFILE
docker build -q -t minio/minio:latest "$build" >/dev/null
rm -rf "$build"
# L'étape de construction (golang:1.24 et l'image intermédiaire) ne sert plus.
docker image rm golang:1.24 >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true
echo "[build_minio_from_source] minio ${MINIO_VERSION}, mc ${MC_VERSION} :" \
  "$(docker run --rm minio/minio:latest --version 2>&1 | head -1) ;" \
  "image $(docker image inspect -f '{{.Size}}' minio/minio:latest | awk '{printf "%.0f Mo", $1/1048576}')"
