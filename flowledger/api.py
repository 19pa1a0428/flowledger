"""HTTP boundary for the workflow domain. Synthetic local demo only."""
from datetime import datetime
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowledger.domain import InvalidTransition, Status, Workflow
from flowledger.repository import InMemoryRepository


class CreateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200, strict=True)
    max_attempts: int = Field(default=3, ge=1, le=100, strict=True)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Workflow name must not be blank")
        return value


class TransitionWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: Status
    occurred_at: datetime
    attempt: int


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    status: Status
    attempts: int
    max_attempts: int
    events: list[EventResponse]


def create_app() -> FastAPI:
    app = FastAPI(title="FlowLedger", version="0.2.0")
    repository = InMemoryRepository()

    @app.post("/workflows", status_code=201, response_model=WorkflowResponse)
    def create_workflow(body: CreateWorkflow, response: Response):
        workflow = repository.add(Workflow.create(body.name, body.max_attempts))
        response.headers["Location"] = f"/workflows/{workflow.id}"
        return workflow

    @app.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
    def get_workflow(workflow_id: UUID):
        try:
            return repository.get(workflow_id)
        except KeyError:
            raise HTTPException(404, "Workflow not found") from None

    @app.post("/workflows/{workflow_id}/transitions", response_model=WorkflowResponse)
    def transition_workflow(workflow_id: UUID, body: TransitionWorkflow):
        try:
            return repository.transition(workflow_id, body.status)
        except KeyError:
            raise HTTPException(404, "Workflow not found") from None
        except InvalidTransition as error:
            raise HTTPException(409, str(error)) from error

    return app


app = create_app()
