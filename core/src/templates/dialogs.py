"""Reusable NiceGUI dialog helpers."""

from typing import Callable

from nicegui import ui


def last_model_warning(on_confirm: Callable) -> None:
    """Show a warning dialog when an action would leave no models available.

    on_confirm is called (and awaited if async) when the admin accepts.
    """
    with ui.dialog() as dlg, ui.card().classes("p-6 max-w-md gap-4"):
        with ui.row().classes("items-center gap-3 mb-1"):
            ui.icon("warning", size="md").classes("text-amber-500 shrink-0")
            ui.label("No models will be available").classes("font-semibold text-base")
        ui.label(
            "This action will leave no models running. "
            "Users will not be able to use the chat until a model is loaded or configured."
        ).classes("text-sm text-gray-600")

        with ui.row().classes("gap-2 justify-end mt-3 w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat")

            async def _accept(d=dlg):
                d.close()
                import asyncio
                coro = on_confirm()
                if asyncio.iscoroutine(coro):
                    await coro

            ui.button("Continue anyway", on_click=_accept).props("unelevated color=warning")

    dlg.open()
