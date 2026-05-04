from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    AGENT_NAME: str = "GATO"
    LLM_MODEL: str = "llama3.2:3b"
    PORT: int = 7720
    MODE: str = "development" # change to "production" to test the switch
    
    DEVELOPMENT_URL_BUTLER_SERVER: str = "http://127.0.0.1:7719"
    PRODUCTION_URL_BUTLER_SERVER: str = "http://147.96.80.104:7719" 
    
    URL_BUTLER_SERVER: str = "http://localhost:11434" # This will be overridden by the model_validator
    EXTERNAL_AGENT_PORT: int = 7720
    OLLAMA_HOST: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def set_url_based_on_mode(self) -> "Config":
        """Dynamically set the URL_BUTLER_SERVER based on the current MODE."""
        if self.MODE == "production":
            self.URL_BUTLER_SERVER = self.PRODUCTION_URL_BUTLER_SERVER
        else:
            self.URL_BUTLER_SERVER = self.DEVELOPMENT_URL_BUTLER_SERVER
        return self

# Instantiate the config
config = Config()

# Quick test to prove it works:
# print(config.URL_BUTLER_SERVER)