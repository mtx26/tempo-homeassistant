"""To-do list entity mapping Tempo study tasks.

Creating an item creates a study task: the summary is the subject, the due date is the
target date (required), the effort is read from the description ("120 min", default 60 min)
and the target is soft (flexible) unless the description contains "hard" or "strict".
Completing or deleting an item archives the task in the backend.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import TempoError
from .const import DEFAULT_TASK_EFFORT_MINUTES, DOMAIN
from .coordinator import TempoConfigEntry, TempoCoordinator
from .entity import TempoEntity

PARALLEL_UPDATES = 0

_EFFORT_RE = re.compile(r"(\d{1,4})\s*min", re.IGNORECASE)
_HARD_RE = re.compile(r"\b(hard|strict|stricte)\b", re.IGNORECASE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tempo to-do list."""
    async_add_entities([TempoTodoListEntity(entry.runtime_data.coordinator)])


def parse_effort(description: str | None) -> int:
    """Return the effort in minutes found in a description, or the default."""
    if description and (match := _EFFORT_RE.search(description)):
        minutes = int(match.group(1))
        if minutes > 0:
            return minutes
    return DEFAULT_TASK_EFFORT_MINUTES


def task_description(task: dict[str, Any]) -> str:
    """Describe the remaining effort and the target kind of a task."""
    kind = (
        "échéance stricte (hard)"
        if task.get("target_kind") == "hard"
        else "date cible souple (soft)"
    )
    return (
        f"Reste {task.get('remaining_minutes', 0)} min sur "
        f"{task.get('effort_minutes', 0)} min ({task.get('completed_minutes', 0)} min faites) "
        f"— {kind}"
    )


def task_to_item(task: dict[str, Any]) -> TodoItem:
    """Map a backend task to a to-do item."""
    due: date | None = None
    if isinstance(task.get("target_date"), str):
        try:
            due = date.fromisoformat(task["target_date"])
        except ValueError:
            due = None
    remaining = int(task.get("remaining_minutes") or 0)
    return TodoItem(
        summary=str(task.get("subject") or ""),
        uid=str(task["id"]),
        status=TodoItemStatus.COMPLETED if remaining <= 0 else TodoItemStatus.NEEDS_ACTION,
        due=due,
        description=task_description(task),
    )


class TempoTodoListEntity(TempoEntity, TodoListEntity):
    """Study tasks of Tempo."""

    _attr_translation_key = "tasks"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: TempoCoordinator) -> None:
        """Initialize the list."""
        super().__init__(coordinator, "tasks")

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the non-archived tasks."""
        return [
            task_to_item(task)
            for task in self.coordinator.data.tasks
            if not task.get("archived") and task.get("id") is not None
        ]

    def _task(self, uid: str) -> dict[str, Any]:
        for task in self.coordinator.data.tasks:
            if str(task.get("id")) == uid:
                return task
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="task_not_found",
            translation_placeholders={"uid": uid},
        )

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a study task (soft target by default)."""
        if not item.summary:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="task_subject_required"
            )
        if item.due is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="task_due_required"
            )
        due = item.due.date() if isinstance(item.due, datetime) else item.due
        payload = {
            "subject": item.summary,
            "effort_minutes": parse_effort(item.description),
            "target_date": due.isoformat(),
            "target_kind": "hard"
            if item.description and _HARD_RE.search(item.description)
            else "soft",
        }
        await self._call(self.coordinator.client.create_task(payload))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Archive completed tasks and update the target date."""
        if item.uid is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="task_not_found",
                translation_placeholders={"uid": "?"},
            )
        task = self._task(item.uid)
        payload: dict[str, Any] = {}
        if item.due is not None:
            due = item.due.date() if isinstance(item.due, datetime) else item.due
            if due.isoformat() != task.get("target_date"):
                payload["target_date"] = due.isoformat()
        if item.status == TodoItemStatus.COMPLETED:
            payload["archived"] = True
        if payload:
            await self._call(self.coordinator.client.update_task(item.uid, payload))

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Archive tasks (the backend keeps their history)."""
        for uid in uids:
            self._task(uid)
        for uid in uids:
            await self._call(
                self.coordinator.client.update_task(uid, {"archived": True}), refresh=False
            )
        await self.coordinator.async_request_refresh()

    async def _call(self, coro: Any, refresh: bool = True) -> None:
        try:
            await coro
        except TempoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="backend_error",
                translation_placeholders={"error": str(err)},
            ) from err
        if refresh:
            await self.coordinator.async_request_refresh()
