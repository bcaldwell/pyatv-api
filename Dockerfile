FROM python
# :alpine

ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# System deps:
# RUN apk update && apk add -U --no-cache build-base ca-certificates libffi-dev openssl-dev cargo

# Copy only requirements to cache them in docker layer
WORKDIR /pyatv-api
COPY poetry.lock pyproject.toml /pyatv-api/

# Project initialization:
RUN pip install poetry && poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi && pip uninstall -y poetry

COPY . /pyatv-api/

# USER app
CMD ["python", "-u", "server.py"]