# APEX — commandes de développement.
#
# `make test` utilise unittest et non pytest : la suite doit pouvoir tourner
# sur un environnement où les dépendances de développement ne sont pas
# installées. C'est la même contrainte que pour le doctor.

.PHONY: help up down launch launch-stop test test-pytest lint types doctor gate clean

help:
	@grep -E '^[a-z-]+:.*?# ' $(MAKEFILE_LIST) | sed 's/:.*# /\t/'

up:              # démarrer l'infrastructure locale
	docker compose up -d
	docker compose ps

down:            # arrêter l'infrastructure (volumes conservés)
	docker compose down

launch:          # lanceur officiel (env + infra + doctor + gate)
	bash scripts/apex-launch.sh

launch-stop:     # arrêter l'infrastructure démarrée par `make launch`
	bash scripts/apex-launch.sh stop

test:            # suite de tests, stdlib uniquement
	python -m unittest discover -s tests -t . -v

test-pytest:     # même suite via pytest (nécessite l'extra dev)
	pytest

lint:            # ruff
	ruff check . && ruff format --check .

types:           # mypy
	mypy apex

doctor:          # diagnostic de l'environnement
	python -m apex.cli.main doctor

gate:            # porte qualité de fin de phase
	@echo "== tests ==";      $(MAKE) --no-print-directory test
	@echo "== lint ==";       $(MAKE) --no-print-directory lint  || true
	@echo "== typage ==";     $(MAKE) --no-print-directory types || true
	@echo "== doctor ==";     $(MAKE) --no-print-directory doctor || true
	@echo "== abstractions =="; python -m apex.cli.main interfaces

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
