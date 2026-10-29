# ExpenseKrafter as a web app.
#
#   docker build -t expensekrafter .
#   docker run -d --name expensekrafter -p 127.0.0.1:8080:8080 \
#       -v ~/Documents/ExpenseKrafter:/data -e EXPENSEKRAFTER_PASSWORD='choose-one' expensekrafter
#
# Everything (database, backups, settings) lives in /data; mount a folder there
# to keep it. A password is required because the container listens on all its
# interfaces; see the README for running it without one on your own PC.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXPENSEKRAFTER_DATA_DIR=/data \
    EXPENSEKRAFTER_CONFIG_DIR=/data/config

WORKDIR /app
COPY pyproject.toml README.md ./
COPY expensekrafter ./expensekrafter
# Web-only install: no desktop window packages (Qt etc.).
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 krafter \
    && mkdir -p /data && chown krafter /data

USER krafter
VOLUME /data
EXPOSE 8080
CMD ["expensekrafter", "--web", "--host", "0.0.0.0", "--port", "8080", "--no-open"]
