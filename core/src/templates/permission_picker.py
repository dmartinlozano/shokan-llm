"""Reusable permission picker widget for NiceGUI."""

from nicegui import ui
from services.permissions import STRUCTURE, SECTION_LABELS


def render_permission_picker(initial_perms: list[str]) -> dict:
    """
    Render a grouped checkbox permission picker.
    Returns {"get_selected": Callable[[], list[str]]}.

    initial_perms: list of permission IDs, or ["*"] for all.
    """
    is_all = "*" in initial_perms
    init_set = set(initial_perms)
    checkboxes: dict[str, ui.checkbox] = {}

    def _is_checked(perm_id: str) -> bool:
        return is_all or perm_id in init_set

    with ui.column().classes("w-full gap-0"):
        for section, resources in STRUCTURE.items():
            with ui.expansion(SECTION_LABELS.get(section, section)).classes("w-full border-b border-slate-100"):
                with ui.column().classes("w-full gap-1 py-1"):
                    for resource, spec in resources.items():
                        actions = spec["actions"]
                        label = spec["label"]
                        with ui.row().classes("items-center gap-2 w-full py-0.5"):
                            ui.label(label).classes("text-sm text-slate-600 w-36 shrink-0")
                            for action in actions:
                                perm_id = f"{section}:{resource}:{action}"
                                short = {
                                    "create": "C",
                                    "read": "R",
                                    "update": "U",
                                    "delete": "D",
                                    "start": "▶",
                                }.get(action, action[0].upper())
                                cb = (
                                    ui.checkbox(short, value=_is_checked(perm_id))
                                    .props("dense")
                                    .tooltip(f"{section}:{resource}:{action}")
                                    .classes("text-xs")
                                )
                                checkboxes[perm_id] = cb

    def get_selected() -> list[str]:
        return [pid for pid, cb in checkboxes.items() if cb.value]

    return {"get_selected": get_selected}
