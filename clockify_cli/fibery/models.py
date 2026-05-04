"""Dataclasses for Fibery push payloads."""
from dataclasses import dataclass
from typing import Optional


def _normalize_dt(dt: Optional[str]) -> Optional[str]:
    """Ensure ISO-8601 datetime has milliseconds as Fibery expects (.000Z)."""
    if not dt:
        return None
    if "." in dt:
        return dt  # already has sub-second precision
    if dt.endswith("Z"):
        return dt[:-1] + ".000Z"
    return dt


@dataclass
class LaborCostPayload:
    """One Clockify time entry mapped to a Fibery Labor Cost entity."""

    time_log_id: str                          # Clockify entry id → Time Log ID (upsert key)
    start_dt: str                             # → Start Date Time
    end_dt: Optional[str]                     # → End Date Time (None for running timers)
    seconds: Optional[int]                    # → Seconds
    hours: Optional[float]                    # → Clockify Hours  (seconds / 3600)
    task: Optional[str]                       # → Task  (description text)
    task_id: Optional[str]                    # → Task ID
    project_id: Optional[str]                 # → Project ID
    billable: str                             # → Billable  "Yes" | "No"
    approval_status: str                      # → Time Entry Status
    user_id_text: Optional[str]               # → User ID  (email)
    user_name: Optional[str]                  # → Time Entry User Name
    project_name: Optional[str]               # → Time Entry Project Name

    def to_fibery_entity(self) -> dict:
        """Build the entity dict for fibery.entity.batch/create-or-update.

        Fibery requires every entity in a batch to carry the *same set of
        fields*.  Optional fields are always present — set to None (JSON null)
        when absent rather than omitted.

        Note: The Clockify User and Agreement relation fields are readonly in
        the Fibery Labor Costs schema and must NOT be included in the payload.
        """
        return {
            "Agreement Management/Time Log ID": self.time_log_id,
            "Agreement Management/Start Date Time": _normalize_dt(self.start_dt),
            "Agreement Management/End Date Time": _normalize_dt(self.end_dt),
            "Agreement Management/Seconds": self.seconds,
            "Agreement Management/Clockify Hours": (
                round(self.hours, 4) if self.hours is not None else None
            ),
            "Agreement Management/Task": self.task,
            "Agreement Management/Task ID": self.task_id,
            "Agreement Management/Project ID": self.project_id,
            "Agreement Management/Billable": self.billable,
            "Agreement Management/Time Entry Status": self.approval_status,
            "Agreement Management/User ID": self.user_id_text,
            "Agreement Management/Time Entry User Name": self.user_name,
            "Agreement Management/Time Entry Project Name": self.project_name,
        }


@dataclass
class PushProgress:
    """Live progress state for a Fibery push run."""

    total: int = 0              # entries to push in this run (incremental or full)
    pushed: int = 0             # entries successfully pushed so far
    created: int = 0            # subset of total that are new (not yet in Fibery)
    updated: int = 0            # subset of total that already exist in Fibery
    skipped: int = 0            # running-timer entries skipped in this run
    errors: int = 0             # entries that failed
    status: str = "pending"     # pending | running | done | error
    phase: str = "pushing"      # deleting | pushing
    error_message: Optional[str] = None
    is_incremental: bool = False  # True when filtered to records changed since last push
    last_pushed_at: Optional[str] = None  # ISO datetime of previous push (None = first push)

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return min(100.0, self.pushed / self.total * 100)

    @property
    def is_done(self) -> bool:
        return self.status in ("done", "error")


@dataclass
class ClockifyUpdateLogResult:
    """Summary values written into Fibery Clockify Update Log."""

    workspace_id: str
    started_at: str
    completed_at: str
    status: str
    total: int
    pushed: int
    created: int
    updated: int
    skipped: int
    errors: int
