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
    user_id_text: Optional[str]               # → User ID  (email)
    user_name: Optional[str]                  # → Time Entry User Name
    project_name: Optional[str]               # → Time Entry Project Name
    clockify_user_fibery_id: Optional[str]    # resolved relation; None if unmatched
    agreement_fibery_id: Optional[str]        # resolved relation; None if unmatched

    def to_fibery_entity(self) -> dict:
        """Build the entity dict for fibery.entity.batch/create-or-update."""
        entity: dict = {
            "Agreement Management/Time Log ID": self.time_log_id,
            "Agreement Management/Start Date Time": _normalize_dt(self.start_dt),
            "Agreement Management/Billable": self.billable,
        }

        if self.end_dt:
            entity["Agreement Management/End Date Time"] = _normalize_dt(self.end_dt)
        if self.seconds is not None:
            entity["Agreement Management/Seconds"] = self.seconds
        if self.hours is not None:
            entity["Agreement Management/Clockify Hours"] = round(self.hours, 4)
        if self.task:
            entity["Agreement Management/Task"] = self.task
        if self.task_id:
            entity["Agreement Management/Task ID"] = self.task_id
        if self.project_id:
            entity["Agreement Management/Project ID"] = self.project_id
        if self.user_id_text:
            entity["Agreement Management/User ID"] = self.user_id_text
        if self.user_name:
            entity["Agreement Management/Time Entry User Name"] = self.user_name
        if self.project_name:
            entity["Agreement Management/Time Entry Project Name"] = self.project_name
        if self.clockify_user_fibery_id:
            entity["Agreement Management/Clockify User"] = {
                "fibery/id": self.clockify_user_fibery_id
            }
        if self.agreement_fibery_id:
            entity["Agreement Management/Agreement"] = {
                "fibery/id": self.agreement_fibery_id
            }

        return entity


@dataclass
class PushProgress:
    """Live progress state for a Fibery push run."""

    total: int = 0          # total entries to push (excluding running timers)
    pushed: int = 0         # entries successfully pushed so far
    skipped: int = 0        # entries skipped (running timers, etc.)
    errors: int = 0         # entries that failed
    status: str = "pending" # pending | running | done | error
    error_message: Optional[str] = None

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return min(100.0, self.pushed / self.total * 100)

    @property
    def is_done(self) -> bool:
        return self.status in ("done", "error")
