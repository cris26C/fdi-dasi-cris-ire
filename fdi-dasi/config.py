from pydantic_settings import BaseSettings

class Config(BaseSettings):
    URL_BUTLER_SERVER: str = "http://127.0.0.1:7719"
    AGENT_NAME: str = "GATO"
    LLM_MODEL: str = "llama3.2:3b"
    PORT: int = 7718
    class Config:
        env_file = ".env" # It will try to load this, but won't crash if missing

config = Config()
