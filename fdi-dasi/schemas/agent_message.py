from pydantic import BaseModel
from typing import Optional

class AgentMessage(BaseModel):
    msg: Optional[str]