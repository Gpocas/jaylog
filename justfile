set dotenv-load := true

default:
  just --list



publish alt='patch':
    echo $PYPI_USER
    echo $PYPI_PASSWORD
    uv version --bump {{alt}}
    uv build && uv publish -u $PYPI_USER -p $PYPI_PASSWORD

