import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from schemas.agent_message import AgentMessage, SendMessage
import asyncio
from contextlib import asynccontextmanager
from loguru import logger
from config import config
from services import (create_agent_and_connect, 
                      get_alias_by_ip, 
                      Agent)
import uvicorn 
import random



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize once
    logger.info(f"Initializing {config.AGENT_NAME}...")
    # app.state.orch = Orchestrator()
    # Parallelizar la creación del agente y envío de mensaje a multiples agentes
    app.state.agent = Agent(config.AGENT_NAME)
    asyncio.create_task(create_agent_and_connect(app.state.agent, config.AGENT_NAME))

    yield

    logger.info(f"{config.AGENT_NAME} shutting down...")

app = FastAPI(lifespan=lifespan)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(
        request,             # El request va primero ahora
        "index.html",        # El nombre del archivo después
        {"user_name": "Cristhian"} # Y el contexto (sin meter el request dentro)
    )
@app.post("/buzon")
async def receive_message(
    agent_message: AgentMessage, 
    request: Request
    ):
    try:
        msg = agent_message.msg
        client_host = request.client.host # type: ignore
        alias = get_alias_by_ip(client_host)
        logger.info(f"Recepción de mensaje desde [{alias}]: {msg}")

        # Process in background so the HTTP response returns immediately.
        # This prevents deadlocks when two agents send messages to each other
        # at the same time — both /buzon endpoints return 200 instantly,
        # and the LLM + outgoing messages are handled asynchronously.
        agent: Agent = app.state.agent
        asyncio.create_task(agent.response(alias, msg))
        logger.info(f">> {client_host} dice: {msg}")
        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error")
    

@app.websocket("/ws/stats")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            agent: Agent = app.state.agent
            memory = agent.get_memory()
            data = {
                "agent_name": agent.name,
                "memory": memory
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    logger.info(f"Starting {config.AGENT_NAME} on port {config.PORT}...")
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)