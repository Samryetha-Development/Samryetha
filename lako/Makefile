.PHONY: dev test build migrate seed
dev:
	docker compose up --build
test:
	cd api && uv run pytest
build:
	cd web && pnpm build
	cd examples/samryetha-client && pnpm build
migrate:
	cd api && uv run alembic upgrade head
seed:
	cd api && uv run python -m app.cli seed
