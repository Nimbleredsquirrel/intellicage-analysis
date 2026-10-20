FROM python:3.14-slim
ENV POETRY_VERSION=2.5.1 POETRY_VIRTUALENVS_CREATE=false PYTHONUNBUFFERED=1
WORKDIR /app
ENV BROWSER_PATH=/usr/bin/chromium
RUN apt-get update && apt-get install -y --no-install-recommends chromium && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"
COPY pyproject.toml poetry.lock ./
RUN poetry sync --only main --extras export --no-root
COPY intellicage ./intellicage
COPY app.py ./
CMD ["python", "app.py", "--web", "--no-show"]
