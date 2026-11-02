from datetime import datetime
from pydantic import BaseModel, Field


class StepMetrics(BaseModel):
    """Measurements and execution trace for one LLM -> sandbox cycle."""

    step: int = Field(ge=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    request_time_ms: float = Field(default=0.0, ge=0)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    api_url: str = ""
    model_name: str = ""
    llm_output: str = ""
    sandbox_input: str = ""
    sandbox_output: str = ""
    retries: int = Field(default=0, ge=0)
