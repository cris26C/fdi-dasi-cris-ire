![Python](https://img.shields.io/badge/python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/framework-fastapi-green)
![Asyncio](https://img.shields.io/badge/concurrency-asyncio-purple)

**Autor(es):** Francisco Mollá Astrar, Jara Blasco Arroyo, Iremar Luhetsy Rivas Álvarez, Christian Iván Cola Pilicita, Lei Siting, Miguel Ángel Osuna Galindo

En el archivo **`ollama_service.py`** se realiza toda la gestión de mensajes con Ollama, así como la obtención de la respuesta.

En **`butler_service.py`** se conecta con el resto de agentes y se envían los mensajes, además de recibir los recursos y la información de Butler.

## INSTRUCCIONES DE LANZAMIENTO

Para ejecutar el programa, hay que preparar ollama:

```
# Solo la primera vez, para instalar lo necesario
./install_ollama.sh

# Para lanzar ollama una vez instalado
./run_ollama.sh
```
Una vez Ollama está instalado y corriendo, hay que lanzar el agente:

```
uv run main.py
```

Para ejecutar desde la raíz del proyecto se debe usar el siguiente comando:

```
    uv run fdi-dasi/main.py
```

Una vez terminado de ejecutar, hay que parar ollama.

```
./stop_ollama.sh
```

## VARIABLES DE ENTORNO

- Configurar variables de entorno en el archivo `.env`:

```
AGENT_NAME = "GATO"
URL_BUTLER_SERVER = "http://127.0.0.1:7719"
URL_BUTLER_SERVER = "http://147.96.80.224:7719"
PORT = 7718
LLM_MODEL = "llama3.2:3b" o "ministral-3:8b"
```

Para ejecutar uv con las variables de entorno, se puede usar el siguiente comando:

```
uv run --env-file .env fdi-dasi/main.py
```

## EJECUTAR PAQUETE BUTLER

- Para ejecutar el paquete de Butler, es necesario seguir las instrucciones del repositorio oficial de Butler:
```
    uv pip install pip fdi-dasi/packages/fdi_pln_butler-26.2.23-py3-none-any.whl
    uv run fdi-pln-butler server
```

docker compose up butler-server
docker compose up butler-server --build

docker compose --profile gato restart agent-one

docker compose --profile perro up --no-deps agent-two

docker compose --profile gato up --no-deps agent-one

docker compose --profile agents down

docker cmpose --profile agents up --build


docker compose --profile agents up --build