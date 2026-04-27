from fastapi import FastAPI, HTTPException, Request
from schemas.agent_message import AgentMessage, SendMessage
import asyncio
from contextlib import asynccontextmanager
from loguru import logger
from config import config
from services import (create_agent_and_connect, 
                      get_alias_by_ip, 
                      send_message_by_alias, 
                      Agent)
import uvicorn 



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize once
    logger.info(f"Initializing {config.AGENT_NAME}...")
    # app.state.orch = Orchestrator()
    # Parallelizar la creación del agente y envío de mensaje a multiples agentes
    app.state.agent = Agent(config.AGENT_NAME)
    app.state.second_agent = Agent(config.AGENT_NAME)
    asyncio.create_task(create_agent_and_connect(app.state.agent, config.AGENT_NAME))

    yield

    logger.info(f"{config.AGENT_NAME} shutting down...")

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
        client_host = request.client.host # type: ignore
        alias = get_alias_by_ip(client_host)
        logger.info(f">>>>>> [{alias}] Mensaje recibido: {msg}")

        # Guardar mensaje en buzón
        agent: Agent = app.state.second_agent
        await agent.response(alias, msg)
        logger.info(f">> {client_host} dice: {msg}")
        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Error")
    
if __name__ == "__main__":
    logger.info(f"Starting {config.AGENT_NAME} on port {config.PORT}...")
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)