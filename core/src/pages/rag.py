"""Data source (RAG) access permissions tab — CRUD via CRUDTemplate."""

import asyncio

from nicegui import ui

from templates.crud_template import CRUDTemplate
from connectors.openfga import OpenFGA
from connectors.rag import RAG

_ROLES = ["owner", "viewer"]


class RagPermissions:
    def __init__(self, fga: OpenFGA, rag: RAG) -> None:
        self.fga = fga
        self.rag = rag

    async def render(self, principals: dict[str, str]) -> None:
        subject_options = principals if principals else {}

        datasources = await asyncio.to_thread(self.rag.list_datasources)
        ds_options = {ds["id"]: f"datasource:{ds['id']} [{ds.get('name', ds['id'])}]" for ds in datasources}

        fields = [
            {"key": "subject", "label": "User / Group", "type": "select", "options": subject_options},
            {"key": "role", "label": "Role", "type": "select", "options": _ROLES},
            {"key": "datasource", "label": "Datasource", "type": "select", "options": ds_options},
        ]

        async def refresh_data():
            sources = await asyncio.to_thread(self.rag.list_datasources)
            if not sources:
                return []
            results = await asyncio.gather(
                *[self.fga.get_object_tuples(f"datasource:{ds['id']}") for ds in sources]
            )
            rows = []
            for ds, tuples in zip(sources, results):
                for subj, rel in tuples.items():
                    if rel in _ROLES:
                        display = (principals or {}).get(subj, subj)
                        ds_label = f"datasource:{ds['id']} [{ds.get('name', ds['id'])}]"
                        rows.append({
                            "subject": display,
                            "role": rel,
                            "datasource": ds_label,
                            "_subject_key": subj,
                            "_ds_id": ds["id"],
                        })
            return rows

        async def on_new(data: dict):
            subj = data.get("subject", "")
            if not subj:
                ui.notify("Select a user or group", type="warning")
                return
            ds_id = data.get("datasource", "")
            await self.fga.set_relation(subj, data.get("role", _ROLES[0]), None, f"datasource:{ds_id}")
            ui.notify("Access granted", type="positive")

        async def on_edit(original: dict, data: dict):
            old_subj = original["_subject_key"]
            old_rel = original["role"]
            old_obj = f"datasource:{original['_ds_id']}"
            new_subj = data.get("subject", old_subj)
            new_ds_id = data.get("datasource", original["_ds_id"])
            new_rel = data.get("role", old_rel)
            new_obj = f"datasource:{new_ds_id}"
            if old_subj == new_subj and old_rel == new_rel and old_obj == new_obj:
                return
            await self.fga.write(
                writes=[{"user": new_subj, "relation": new_rel, "object": new_obj}],
                deletes=[{"user": old_subj, "relation": old_rel, "object": old_obj}],
            )
            ui.notify("Access updated", type="positive")

        async def on_delete(item: dict):
            await self.fga.remove_relation(
                item["_subject_key"], item["role"], f"datasource:{item['_ds_id']}"
            )
            ui.notify("Access removed", type="info")

        CRUDTemplate(
            title="Data Source Permissions",
            columns=["Subject", "Role", "Datasource"],
            on_refresh=refresh_data,
            on_new=on_new,
            on_edit=on_edit,
            on_delete=on_delete,
            fields=fields,
        )
