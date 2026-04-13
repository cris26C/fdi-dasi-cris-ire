from pydantic import BaseModel
from typing import Optional

class AgentMessage(BaseModel):
    msg: Optional[str]

class SendMessage(BaseModel):
    message: Optional[str]
    alias: Optional[str]