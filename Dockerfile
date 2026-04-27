# Usar una imagen oficial de Python compatible con pyproject.toml (requires-python >= 3.12)
FROM python:3.12-slim

# Copiar uv desde su imagen oficial
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Establecer el directorio de trabajo dentro del contenedor
WORKDIR /app

# Evitar que Python genere archivos .pyc y forzar que los logs salgan en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copiar el manifiesto de dependencias y sincronizar el entorno virtual
COPY pyproject.toml .
RUN uv sync --no-dev

# Copiar el código fuente e instalar el paquete .whl local
COPY fdi-dasi/ ./fdi-dasi/
RUN uv pip install fdi-dasi/packages/fdi_pln_butler-26.2.23-py3-none-any.whl

# Cambiar al directorio donde está main.py
WORKDIR /app/fdi-dasi

# Exponer el puerto por defecto de config.PORT
EXPOSE 7718

# Arrancar FastAPI con uv run para usar el entorno virtual gestionado por uv
CMD ["uv", "run", "--project", "/app", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7718"]