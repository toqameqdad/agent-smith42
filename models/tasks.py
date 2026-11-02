from pydantic import BaseModel, Field


class MBPPTaskInput(BaseModel):
    """Input task shape produced by the MBPP moulinette."""

    task_id: int
    task_definition: str
    function_definition: str
    test_imports: list[str] = Field(default_factory=list)
    test_list: list[str] = Field(default_factory=list)


class SWEBenchTaskInput(BaseModel):
    """Input task shape produced by the SWE-bench moulinette."""

    instance_id: str
    problem_statement: str
    docker_image: str
    eval_script: str
    hints_text: str = ""
    repo: str = ""
