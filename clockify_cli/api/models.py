"""Pydantic models for Clockify API responses."""
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class TimeInterval(BaseModel):
    start: str
    end: Optional[str] = None
    duration: Optional[str] = None  # ISO 8601 e.g. "PT1H30M"


class Workspace(BaseModel):
    id: str
    name: str
    currency_code: Optional[str] = Field(None, alias="currencyCode")
    image_url: Optional[str] = Field(None, alias="imageUrl")

    model_config = {"populate_by_name": True}

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "currency_code": self.currency_code,
            "image_url": self.image_url,
        }


class Client(BaseModel):
    id: str
    name: str
    workspace_id: str = Field(alias="workspaceId")
    archived: bool = False

    model_config = {"populate_by_name": True}

    def to_db_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "archived": self.archived}


class Project(BaseModel):
    id: str
    name: str
    workspace_id: str = Field(alias="workspaceId")
    client_id: Optional[str] = Field(None, alias="clientId")
    color: Optional[str] = None
    archived: bool = False
    billable: bool = False
    public: bool = False

    model_config = {"populate_by_name": True}

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "clientId": self.client_id,
            "color": self.color,
            "archived": self.archived,
            "billable": self.billable,
            "public": self.public,
        }


class User(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    status: Optional[str] = None  # ACTIVE | INACTIVE
    profile_picture: Optional[str] = Field(None, alias="profilePicture")

    model_config = {"populate_by_name": True}

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "status": self.status,
            "profilePicture": self.profile_picture,
        }


class WorkspaceMembership(BaseModel):
    """Membership info embedded in a user object from the workspace members endpoint."""
    user_id: str = Field(alias="userId")
    target_id: Optional[str] = Field(None, alias="targetId")
    role: Optional[str] = None
    status: Optional[str] = None

    model_config = {"populate_by_name": True}


class WorkspaceUser(BaseModel):
    """User returned from GET /workspaces/{id}/users."""
    id: str
    name: str = ""
    email: Optional[str] = None
    status: Optional[str] = None
    profile_picture: Optional[str] = Field(None, alias="profilePicture")

    model_config = {"populate_by_name": True}

    @field_validator("name", mode="before")
    @classmethod
    def coerce_null_name(cls, v: Any) -> str:
        """Clockify sometimes returns null for name on pending/invited users."""
        return v if isinstance(v, str) else ""

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "status": self.status,
            "profilePicture": self.profile_picture,
        }


class TimeEntry(BaseModel):
    id: str
    workspace_id: str = Field(alias="workspaceId")
    user_id: Optional[str] = Field(None, alias="userId")
    project_id: Optional[str] = Field(None, alias="projectId")
    task_id: Optional[str] = Field(None, alias="taskId")
    description: Optional[str] = None
    billable: bool = False
    is_locked: bool = Field(False, alias="isLocked")
    time_interval: TimeInterval = Field(alias="timeInterval")
    tag_ids: list[str] = Field(default_factory=list, alias="tagIds")

    model_config = {"populate_by_name": True}

    @field_validator("tag_ids", mode="before")
    @classmethod
    def coerce_null_tag_ids(cls, v: Any) -> list[str]:
        """Clockify sometimes returns null instead of [] for tagIds."""
        if v is None:
            return []
        return v  # type: ignore[return-value]

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "userId": self.user_id,
            "projectId": self.project_id,
            "taskId": self.task_id,
            "description": self.description,
            "billable": self.billable,
            "isLocked": self.is_locked,
            "tagIds": self.tag_ids,
            "timeInterval": {
                "start": self.time_interval.start,
                "end": self.time_interval.end,
                "duration": self.time_interval.duration,
            },
        }
