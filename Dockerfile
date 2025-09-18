ARG BASE_IMAGE=ghcr.io/home-assistant/aarch64-base-python
ARG PYTHON_VERSION=3.13
ARG ALPINE_VERSION=3.22

FROM ${BASE_IMAGE}:${PYTHON_VERSION}-alpine${ALPINE_VERSION}

WORKDIR /app

RUN apk add --no-cache jq yq udev curl

COPY /static /app/static
COPY backend.py /app
COPY cc2538_bsl.py /app
COPY requirements.txt /app

RUN pip install -r requirements.txt
