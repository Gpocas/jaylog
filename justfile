set dotenv-load := true

default:
  just --list

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

test:
    uv run pytest


publish alt='patch': test
    echo $PYPI_USER
    echo $PYPI_PASSWORD
    uv version --bump {{alt}}
    uv build && uv publish -u $PYPI_USER -p $PYPI_PASSWORD
