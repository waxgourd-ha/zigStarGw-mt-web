ARG BASE_IMAGE=
ARG PYTHON_VERSION=
ARG ALPINE_VERSION=

FROM ${BASE_IMAGE}:${PYTHON_VERSION}-alpine${ALPINE_VERSION}

WORKDIR /app

RUN apk add --no-cache jq yq udev curl

COPY /static /app/static
COPY backend.py /app
COPY cc2538_bsl.py /app
COPY requirements.txt /app

RUN pip install -r requirements.txt
