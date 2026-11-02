from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from models.metrics import StepMetrics


class SolutionOutput(BaseModel):
    """Common result format for MBPP and SWE-bench agents."""

    task_id: str
    benchmark: Literal["mbpp", "swebench"]
    success: bool
    solution: str = ""
    iterations: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    total_time_seconds: float = Field(default=0.0, ge=0)
    steps: list[StepMetrics] = Field(default_factory=list)
    system_prompt: str = ""
    error: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
