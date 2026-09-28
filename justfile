set dotenv-load := true

default:
  just --list

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

test:
    uv run pytest

publish: test
    echo $PYPI_USER
    echo $PYPI_PASSWORD
    uv build && uv publish -u $PYPI_USER -p $PYPI_PASSWORD
