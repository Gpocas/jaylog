set dotenv-load := true

default:
  just --list

publish:
    echo $PYPI_USER
    echo $PYPI_PASSWORD
    uv publish -u $PYPI_USER -p $PYPI_PASSWORD

