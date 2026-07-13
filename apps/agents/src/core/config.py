from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    aiv_env: str = "dev"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/aiveridia"

    # LLM router
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "aiveridia-events:8b"
    aiveridia_events_model_arn: str = ""
    langsmith_project: str = "aiveridia-events-dev"

    # Reglas de negocio
    aiv_umbral_descuento: float = 10.0

    # Timeouts BPMN (horas) — P1: 48 h cotización, 7 días final, 24/48 h pago
    aiv_timeout_cotizacion_h: float = 48
    aiv_timeout_final_h: float = 168
    aiv_timeout_pago_h: float = 24
    aiv_timeout_pago_final_h: float = 48

    # Borde
    aiv_timers: str = "null"            # null | apscheduler | eventbridge
    aiv_dedup: str = "postgres"         # postgres | memory
    aiv_internal_token: str = "dev-internal-token"
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_access_token: str = ""
    culqi_webhook_secret: str = ""
    mercadopago_webhook_secret: str = ""
    resume_url: str = "http://localhost:8000/internal/resume"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
