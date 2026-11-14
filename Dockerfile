# Container image for the hosted app (Phase 11; deployment design Section 1; D-193, D-194).
# Build context: the allow-list in .dockerignore. Nothing outside that list reaches the image.
FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    TAXY_DEPLOYMENT=hosted

# Non-root user, uid and gid 1000 (Render reads secret files as group 1000).
RUN groupadd --gid 1000 taxy \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin taxy

WORKDIR /srv/taxy

# The tested dependency set, installed exactly as locked (D-194), then a consistency check.
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --no-deps -r requirements.lock.txt \
    && pip check

# Project files (allow-listed). They belong to root and are read-only to the app user.
COPY . .

# Streamlit looks for its sign-in secrets here; the file exists only at run time (Render secret file).
RUN ln -s /etc/secrets/secrets.toml /srv/taxy/.streamlit/secrets.toml

USER taxy
EXPOSE 10000

CMD ["sh", "-c", "exec streamlit run app/main.py --server.port=${PORT:-10000} --server.address=0.0.0.0 --server.headless=true --server.fileWatcherType=none --client.showErrorDetails=none"]
