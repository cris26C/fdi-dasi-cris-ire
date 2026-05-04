FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY fdi-dasi/packages/ ./packages/

RUN uv sync --frozen --no-install-project

COPY . .

RUN uv sync --frozen

RUN uv pip install ./packages/fdi_pln_butler-26.2.23-py3-none-any.whl

EXPOSE 8000 7719

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]