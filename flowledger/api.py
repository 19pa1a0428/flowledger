"""HTTP boundary for the workflow domain. Synthetic local demo only."""
from datetime import datetime
import os
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine

from flowledger.auth import Principal, Role, TokenAuthenticator
from flowledger.domain import InvalidTransition, Status, Workflow
from flowledger.repository import ConcurrencyConflict, SqlAlchemyRepository, build_session_factory


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
    expected_version: int = Field(ge=0, strict=True)


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
    version: int
    events: list[EventResponse]


def create_app(repository: SqlAlchemyRepository | None = None,
               authenticator: TokenAuthenticator | None = None) -> FastAPI:
    app = FastAPI(title="FlowLedger", version="0.4.0")
    authenticator = authenticator or TokenAuthenticator.from_json(
        os.getenv("FLOWLEDGER_AUTH_TOKENS")
    )
    if repository is None:
        database_url = os.getenv(
            "FLOWLEDGER_DATABASE_URL",
            "postgresql+psycopg://flowledger:flowledger@localhost/flowledger",
        )
        repository = SqlAlchemyRepository(build_session_factory(create_engine(database_url)))

    bearer = HTTPBearer(auto_error=False)

    def current_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Principal:
        principal = (authenticator.authenticate(credentials.credentials)
                     if credentials is not None else None)
        if principal is None:
            raise HTTPException(401, "Not authenticated",
                                headers={"WWW-Authenticate": "Bearer"})
        return principal

    def require_operator(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role is not Role.OPERATOR:
            raise HTTPException(403, "Operator role required")
        return principal

    @app.post("/workflows", status_code=201, response_model=WorkflowResponse,
              dependencies=[Depends(require_operator)])
    def create_workflow(body: CreateWorkflow, response: Response):
        workflow = repository.add(Workflow.create(body.name, body.max_attempts))
        response.headers["Location"] = f"/workflows/{workflow.id}"
        return workflow

    @app.get("/workflows/{workflow_id}", response_model=WorkflowResponse,
             dependencies=[Depends(current_principal)])
    def get_workflow(workflow_id: UUID):
        try:
            return repository.get(workflow_id)
        except KeyError:
            raise HTTPException(404, "Workflow not found") from None

    @app.post("/workflows/{workflow_id}/transitions", response_model=WorkflowResponse,
              dependencies=[Depends(require_operator)])
    def transition_workflow(workflow_id: UUID, body: TransitionWorkflow):
        try:
            return repository.transition(workflow_id, body.status, body.expected_version)
        except KeyError:
            raise HTTPException(404, "Workflow not found") from None
        except InvalidTransition as error:
            raise HTTPException(409, str(error)) from error
        except ConcurrencyConflict as error:
            raise HTTPException(409, str(error)) from error

    return app


app = create_app()
