"""Progress tracking dataclasses for the sync orchestrator."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

EntityType = Literal["clients", "projects", "users", "time_entries"]


@dataclass
class EntityProgress:
    entity: EntityType
    status: Literal["pending", "running", "done", "error"] = "pending"
    records_fetched: int = 0
    records_upserted: int = 0
    current_page: int = 0
    total_pages: int = 0  # 0 = unknown
    error: Optional[str] = None

    @property
    def percent(self) -> float:
        if self.total_pages > 0:
            return min(100.0, self.current_page / self.total_pages * 100)
        return 0.0

    @property
    def is_done(self) -> bool:
        return self.status in ("done", "error")


@dataclass
class SyncProgress:
    workspace_id: str
    incremental: bool
    entities: dict[EntityType, EntityProgress] = field(default_factory=dict)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    overall_error: Optional[str] = None

    def __post_init__(self) -> None:
        for entity in ("clients", "projects", "users", "time_entries"):
            e = entity  # type: ignore[assignment]
            if e not in self.entities:
                self.entities[e] = EntityProgress(entity=e)  # type: ignore[arg-type]

    @property
    def is_done(self) -> bool:
        return all(ep.is_done for ep in self.entities.values())

    @property
    def total_records(self) -> int:
        return sum(ep.records_upserted for ep in self.entities.values())

    @property
    def has_errors(self) -> bool:
        return any(ep.status == "error" for ep in self.entities.values()) or bool(
            self.overall_error
        )
