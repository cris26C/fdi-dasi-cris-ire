from fastapi import FastAPI, WebSocket, Request, BackgroundTasks, HTTPException, Depends, WebSocketDisconnect, status
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from schemas.agent_message import AgentMessage
from contextlib import asynccontextmanager
from loguru import logger
from core.config import config
from services import ButlerService, Agent
import uvicorn
import asyncio
import os

def get_current_agent(request: Request) -> 'Agent':
    return request.app.state.agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Initializing {config.AGENT_NAME}...")
    butler_service = ButlerService()
    app.state.butler_service = butler_service
    app.state.agent = Agent(config.AGENT_NAME, butler_service)
    task = asyncio.create_task(butler_service.create_agent_and_connect(app.state.agent, config.AGENT_NAME))

    yield

    logger.info(f"{config.AGENT_NAME} shutting down...")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
app = FastAPI(lifespan=lifespan)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"user_name": "USUARIO"})

@app.post("/buzon", status_code=status.HTTP_202_ACCEPTED)
async def receive_message(
    agent_message: AgentMessage, 
    request: Request,
    background_tasks: BackgroundTasks,
    agent: 'Agent' = Depends(get_current_agent)
):
    try:
        msg = agent_message.msg
        client_host = request.client.host if request.client else "unknown"
        
        alias = ButlerService.get_alias_by_ip(client_host)

        logger.info(f"Recepción de mensaje desde [{alias}] ({client_host}): {msg}")

        background_tasks.add_task(agent.response, alias, msg)
        
        return {"status": "accepted", "detail": "Mensaje encolado para procesamiento"}
        
    except HTTPException:
        raise 
    except Exception:
        logger.exception("Error al encolar el mensaje en el buzón")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Error interno al procesar el mensaje")

@app.websocket("/ws/stats")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            agent: Agent = app.state.agent
            butler_service: ButlerService = app.state.butler_service
            memory = agent.get_memory()
            resources = butler_service.get_actual_resources_and_objectives()
            data = {
                "agent_name": agent.name,
                "memory": memory,
                "resources": resources,
                "errors": agent.get_errors(),
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    logger.info(f"Starting {config.AGENT_NAME} on port {config.PORT}...")
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)