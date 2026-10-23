
from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    authorized_imports: list[str] = Field(
        default_factory=lambda: [
            "math", "math.*",
            "collections", "collections.*",
            "itertools", "re", "json",
            "typing", "typing.*",
            "functools", "operator",
            "heapq", "bisect", "copy",
            "string", "random",
            "datetime", "datetime.*",
            "array", "cmath",
        ]
    )

    allowed_directories: list[str] = Field(
        default_factory=lambda: [
            "/testbed",
            "/tmp/agent",
        ]
    )

    max_execution_time_seconds: int = Field(default=30, ge=1)
    max_memory_mb: int = Field(default=512, ge=1)
