FROM python:3.11-slim

WORKDIR /app

# Install uv and keep the virtualenv outside /app so the bind mount in
# docker-compose.yml does not hide installed dependencies.
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Копируем проект целиком, чтобы миграции и конфиги были доступны и без bind mount
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]
