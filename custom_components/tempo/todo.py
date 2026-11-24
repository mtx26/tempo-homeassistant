"""Read-only to-do list showing Tempo's remaining work (tasks and study volumes).

Passive display only: items cannot be created, edited or deleted here. Tasks are managed by
conversation with the Tempo assistant ("ajoute 15 h de physique avant vendredi", "j'ai fait
45 minutes de physique"), which applies the business rules and the work log.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TempoConfigEntry, TempoCoordinator
from .entity import TempoEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tempo remaining-work list."""
    async_add_entities([TempoTodoListEntity(entry.runtime_data.coordinator)])


def task_description(task: dict[str, Any]) -> str:
    """Describe the remaining work and the kind of target of a task."""
    effort = task.get("estimated_minutes")
    if effort is None:
        return "Durée inconnue : précisez-la à Tempo pour qu'elle soit planifiée"
    kind = (
        "échéance stricte"
        if task.get("hard_deadline")
        else "date cible souple"
        if task.get("target_date")
        else "sans échéance"
    )
    return (
        f"Reste {task.get('remaining_minutes', 0)} min sur {effort} min "
        f"({task.get('done_minutes', 0)} min faites) — {kind}"
    )


def task_to_item(task: dict[str, Any]) -> TodoItem:
    """Map a backend task to a to-do item."""
    due: date | None = None
    if isinstance(task.get("target_date"), str):
        try:
            due = date.fromisoformat(task["target_date"])
        except ValueError:
            due = None
    remaining = task.get("remaining_minutes")
    done = remaining is not None and int(remaining) <= 0
    return TodoItem(
        summary=str(task.get("title") or ""),
        uid=str(task["id"]),
        status=TodoItemStatus.COMPLETED if done else TodoItemStatus.NEEDS_ACTION,
        due=due,
        description=task_description(task),
    )


class TempoTodoListEntity(TempoEntity, TodoListEntity):
    """Remaining work of Tempo (read-only)."""

    _attr_translation_key = "tasks"
    _attr_supported_features = TodoListEntityFeature(0)

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
