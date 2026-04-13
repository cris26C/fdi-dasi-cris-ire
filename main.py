from fastapi import FastAPI, HTTPException, Request
from schemas.agent_message import AgentMessage, SendMessage
from services.butler_service import create_agent_and_connect, get_alias_by_ip, send_message_by_alias
from services.ollama_service import Orchestrator
import asyncio
from contextlib import asynccontextmanager
from loguru import logger
from constants import AGENT_NAME, URL_SERVER
import uvicorn 



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize once
    logger.info(f"Initializing {AGENT_NAME}...")
    app.state.orch = Orchestrator()
    # Parallelizar la creación del agente y envío de mensaje a multiples agentes
    task = asyncio.create_task(create_agent_and_connect(app.state.orch, AGENT_NAME))

    yield

    task.cancel()

    logger.info(f"{AGENT_NAME} shutting down...")

app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/buzon")
async def receive_message(
    agent_message: AgentMessage, 
    request: Request
    ):
    try:
        msg = agent_message.msg
        logger.info(f"Mensaje recibido: {msg}")
        client_host = request.client.host
        alias = get_alias_by_ip(client_host)
        # Guardar mensaje en buzón
        await app.state.orch.save_message(alias, msg)
        logger.info(f">> {client_host} dice: {msg}")
        return True       
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error")

@app.post("/send-message")
async def send_message(
        data: SendMessage
    ):
    try:
        response = send_message_by_alias(data.message, data.alias)
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error")
    
if __name__ == "__main__":
    # await orch.start()
    # r1 = asyncio.create_task(orch.send("agent1", "Hola"))
    # print(await r1)

    uvicorn.run("main:app", host="0.0.0.0", port=7720, reload=True)

