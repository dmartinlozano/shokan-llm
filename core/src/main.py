"""
Shokan-LLM NiceGUI application — entrypoint.

Hosts all pages: Chat (/), Profile (/profile), Permissions (/settings),
LiteLLM (/litellm-settings), Data Lake (/datalake-settings), System (/system-settings).
Auth: OIDC via authlib + Starlette SessionMiddleware.
"""

import asyncio

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.sessions import SessionMiddleware

from config import (
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    KC_URL,
    KC_EXTERNAL_URL,
    KC_REALM,
    SESSION_SECRET,
    SHOKAN_URL,
)
from connectors.k8s import K8s
from connectors.keycloak import Keycloak
from connectors.openfga import SHOKAN_OBJECT, OpenFGA
from services.permissions import UIPermService, can

_ADMIN_USERNAMES: frozenset[str] = frozenset({"shokan-admin", "shokan-svc"})
from pages.chat import ChatPage
from pages.datalake import DatalakeView
from pages.models import LitellmView
from pages.permissions import PermissionsView
from pages.profile import ProfileView
from pages.system import SystemView

# ── Singletons ─────────────────────────────────────────────────────────────────

_fga = OpenFGA()
_kc = Keycloak()
_k8s = K8s()
_ui_perm_svc = UIPermService(_fga)
_settings_permissions = PermissionsView()
_settings_profile = ProfileView()
_settings_litellm = LitellmView()
_settings_datalake = DatalakeView()
_settings_system = SystemView()
_chat_page = ChatPage()

# ── Middleware & OAuth ─────────────────────────────────────────────────────────

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

_oauth = OAuth()
_oauth.register(
    name="keycloak",
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    # Server-side calls (token exchange, JWKS) use the internal cluster URL so they
    # never leave the cluster and don't depend on ingress or external DNS.
    access_token_url=f"{KC_URL}/realms/{KC_REALM}/protocol/openid-connect/token",
    jwks_uri=f"{KC_URL}/realms/{KC_REALM}/protocol/openid-connect/certs",
    # The authorize redirect goes to the browser, so it must use the external URL.
    authorize_url=f"{KC_EXTERNAL_URL}/realms/{KC_REALM}/protocol/openid-connect/auth",
    # Issuer in the ID token is set by Keycloak to the external hostname.
    issuer=f"{KC_EXTERNAL_URL}/realms/{KC_REALM}",
    client_kwargs={"scope": "openid profile email"},
)

# ── Auth routes ────────────────────────────────────────────────────────────────


@app.get("/login")
async def login(request: Request):
    redirect_uri = f"{SHOKAN_URL}/auth/callback"
    return await _oauth.keycloak.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await _oauth.keycloak.authorize_access_token(request)
    userinfo = token.get("userinfo") or await _oauth.keycloak.parse_id_token(request, token)
    user = {
        "id": userinfo.get("sub", ""),
        "username": userinfo.get("preferred_username", userinfo.get("sub", "")),
        "email": userinfo.get("email", ""),
        "name": userinfo.get("name", ""),
    }
    request.session["user"] = user
    request.session["id_token"] = token.get("id_token", "")
    await _assign_default_role(user)
    return RedirectResponse("/")


async def _assign_default_role(user: dict) -> None:
    """On first login, assign admin (for service accounts) or member to new users."""
    import logging as _log
    user_id = user.get("id", "")
    if not user_id:
        return
    try:
        tuples = await _fga.get_object_tuples(SHOKAN_OBJECT)
        if f"user:{user_id}" not in tuples:
            role = "admin" if user.get("username") in _ADMIN_USERNAMES else "member"
            await _fga.write(
                writes=[{"user": f"user:{user_id}", "relation": role, "object": SHOKAN_OBJECT}]
            )
    except Exception as exc:
        _log.getLogger(__name__).warning(
            "Could not assign default role for user '%s': %s — they may see access errors until next login.",
            user.get("username", user_id), exc,
        )


@app.get("/logout")
async def logout(request: Request):
    id_token = request.session.get("id_token", "")
    request.session.clear()
    kc_logout = (
        f"{KC_EXTERNAL_URL}/realms/{KC_REALM}/protocol/openid-connect/logout"
        f"?post_logout_redirect_uri={SHOKAN_URL}/login"
        f"&client_id={OIDC_CLIENT_ID}"
    )
    if id_token:
        kc_logout += f"&id_token_hint={id_token}"
    return RedirectResponse(kc_logout)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── A2A endpoints ──────────────────────────────────────────────────────────────


@app.get("/.well-known/agent.json")
async def a2a_agent_card():
    from connectors.a2a import handle_agent_card
    return await handle_agent_card(_k8s)


@app.post("/a2a/tasks")
async def a2a_task(request: Request):
    from connectors.a2a import handle_task
    return await handle_task(request, _k8s)


# ── Auth helpers ───────────────────────────────────────────────────────────────


def _get_user(request: Request) -> dict | None:
    return request.session.get("user")


async def _guard_user(request: Request) -> dict | None:
    user = _get_user(request)
    if not user:
        ui.navigate.to("/login")
        return None
    return user


async def _get_user_perms(user: dict) -> set[str]:
    """Get user's effective UI permissions (direct role wins; else union of group roles)."""
    tuples = await _fga.get_object_tuples(SHOKAN_OBJECT)
    direct_role = tuples.get(f"user:{user['id']}")
    group_roles: list[str] = []
    if not direct_role:
        try:
            kc_groups = await _kc.list_user_groups(user["id"])
            if kc_groups:
                tuples_multi = await _fga.get_object_tuples_multi(SHOKAN_OBJECT)
                for g in kc_groups:
                    group_roles.extend(tuples_multi.get(f"group:{g['id']}#member", []))
        except Exception:
            pass
    return await _ui_perm_svc.effective_for_user(user["id"], direct_role, group_roles or None)


# ── Shared navigation ──────────────────────────────────────────────────────────


async def _render_nav(user: dict, perms: set[str]) -> None:
    drawer = ui.left_drawer(top_corner=True, bottom_corner=True).classes("bg-gray-800 text-white w-56")

    with ui.header().classes(
        "h-10 min-h-0 items-center px-2 gap-0 bg-gray-800 shadow-none"
    ).style("z-index: 201"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense").classes("text-white")

    with drawer:
        with ui.column().classes("p-4 gap-1 w-full h-full"):
            ui.label("Shokan").classes("text-xl font-bold text-white mb-4")

            ui.button("Chat", icon="chat", on_click=lambda: ui.navigate.to("/")).props(
                "flat align=left"
            ).classes("w-full text-white")

            ui.button("Profile", icon="person", on_click=lambda: ui.navigate.to("/profile")).props(
                "flat align=left"
            ).classes("w-full text-white")

            if can(perms, "models:menu:read"):
                ui.button(
                    "Models",
                    icon="model_training",
                    on_click=lambda: ui.navigate.to("/litellm-settings"),
                ).props("flat align=left").classes("w-full text-white")

            if can(perms, "datalake:menu:read"):
                ui.button(
                    "Data Lake",
                    icon="storage",
                    on_click=lambda: ui.navigate.to("/datalake-settings"),
                ).props("flat align=left").classes("w-full text-white")

            if can(perms, "settings:menu:read"):
                ui.button(
                    "Permissions",
                    icon="settings",
                    on_click=lambda: ui.navigate.to("/settings"),
                ).props("flat align=left").classes("w-full text-white")

            if can(perms, "system:menu:read"):
                ui.button(
                    "System",
                    icon="tune",
                    on_click=lambda: ui.navigate.to("/system-settings"),
                ).props("flat align=left").classes("w-full text-white")

            ui.space()

            with ui.row().classes("items-center gap-2 px-2"):
                ui.icon("account_circle", size="sm").classes("text-gray-400")
                ui.label(user.get("username", "")).classes("text-sm text-gray-300 truncate max-w-32")

            ui.button("Logout", icon="logout", on_click=lambda: ui.run_javascript("window.location.href='/logout'")).props(
                "flat dense align=left"
            ).classes("w-full text-xs text-gray-400")


# ── Pages ──────────────────────────────────────────────────────────────────────


@ui.page("/")
async def chat_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    if not await _fga.check(user["id"], "can_use_ai", SHOKAN_OBJECT):
        ui.navigate.to("/forbidden")
        return
    perms = await _get_user_perms(user)
    await _render_nav(user, perms)
    await _chat_page.render(user, perms)


@ui.page("/settings")
async def settings_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    perms = await _get_user_perms(user)
    if not can(perms, "settings:menu:read"):
        ui.navigate.to("/forbidden")
        return
    await _render_nav(user, perms)
    with ui.column().classes("w-full p-4"):
        await _settings_permissions.render(user, perms)


@ui.page("/profile")
async def profile_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    perms = await _get_user_perms(user)
    await _render_nav(user, perms)
    with ui.column().classes("w-full p-4"):
        await _settings_profile.render(user, perms)


@ui.page("/litellm-settings")
async def litellm_settings_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    perms = await _get_user_perms(user)
    if not can(perms, "models:menu:read"):
        ui.navigate.to("/forbidden")
        return
    await _render_nav(user, perms)
    with ui.column().classes("w-full p-4"):
        await _settings_litellm.render(user, perms)


@ui.page("/datalake-settings")
async def datalake_settings_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    perms = await _get_user_perms(user)
    if not can(perms, "datalake:menu:read"):
        ui.navigate.to("/forbidden")
        return
    await _render_nav(user, perms)
    with ui.column().classes("w-full p-4"):
        await _settings_datalake.render(user, perms)


@ui.page("/system-settings")
async def system_settings_page(request: Request) -> None:
    user = await _guard_user(request)
    if not user:
        return
    perms = await _get_user_perms(user)
    if not can(perms, "system:menu:read"):
        ui.navigate.to("/forbidden")
        return
    await _render_nav(user, perms)
    with ui.column().classes("w-full p-4"):
        await _settings_system.render(user, perms)



@ui.page("/forbidden")
async def forbidden_page(request: Request) -> None:
    with ui.column().classes("items-center justify-center h-screen w-full gap-4"):
        ui.icon("lock", size="xl").classes("text-red-400")
        ui.label("Access denied").classes("text-2xl font-bold")
        ui.label("You don't have permission to view this page.").classes("text-gray-500")
        ui.button("Go home", icon="home", on_click=lambda: ui.navigate.to("/")).props("flat")


# ── Startup model sync ────────────────────────────────────────────────────────


@app.on_startup
async def _seed_default_skill() -> None:
    """Write the bundled default skill on first boot if no skills exist yet."""
    from pathlib import Path
    from services.skills import SkillsStorage

    store = SkillsStorage()
    if store.list_skills():
        return  # skills already present — do not overwrite user changes

    seed = Path(__file__).parent / "skills" / "default.md"
    if not seed.exists():
        return

    import re, time
    text = seed.read_text(encoding="utf-8")
    fm_re = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
    fm = fm_re.match(text)
    name = "Shokan Platform Assistant"
    if fm:
        for line in fm.group(1).splitlines():
            if line.startswith("name: "):
                name = line[len("name: "):]
        content = text[fm.end():].strip()
    else:
        content = text.strip()

    sid = store.create_skill(name=name, content=content)
    print(f"[startup] seeded default skill '{name}' ({sid})", flush=True)


@app.on_startup
async def _sync_litellm_on_startup() -> None:
    """Re-register all configured cloud models into LiteLLM after (re)start."""
    from config import DEFAULT_MODEL
    from connectors.litellm import LiteLLM
    from connectors.ollama import Ollama
    from services.litellm_config import LiteLLMConfig, sync_configured_models

    if SESSION_SECRET == "change-me-in-production":
        print(
            "[startup] WARNING: SESSION_SECRET is using the insecure default. "
            "Set SESSION_SECRET in your deployment before going to production.",
            flush=True,
        )

    from connectors.rag import set_top_k
    stored_top_k = await asyncio.to_thread(_k8s.read, "rag-top-k")
    if stored_top_k:
        try:
            set_top_k(int(stored_top_k))
            print(f"[startup] RAG top_k restored to {stored_top_k}", flush=True)
        except ValueError:
            pass

    await asyncio.sleep(5)  # let LiteLLM finish its own startup
    litellm = LiteLLM()
    cfg = LiteLLMConfig(_k8s)
    n = await sync_configured_models(litellm, cfg, _fga)
    if n:
        print(f"[startup] synced {n} model(s) to LiteLLM", flush=True)

    # Auto-load all installed Ollama models on startup (keep_alive=-1 so they stay in RAM)
    try:
        ollama = Ollama()
        local = await ollama.list_local()
        if local:
            running_names = {m["name"] for m in await ollama.running_models()}
            default_name = DEFAULT_MODEL[len("ollama/"):] if DEFAULT_MODEL.startswith("ollama/") else None

            # Load DEFAULT_MODEL first so it's ready immediately
            if default_name:
                if default_name in {m["name"] for m in local}:
                    if default_name not in running_names:
                        print(f"[startup] auto-loading default model '{default_name}'…", flush=True)
                        await ollama.load(default_name)
                        running_names.add(default_name)
                        print(f"[startup] default model '{default_name}' loaded.", flush=True)
                else:
                    print(
                        f"[startup] WARNING: DEFAULT_MODEL '{default_name}' not installed — "
                        "chat will use the first available model.",
                        flush=True,
                    )

            # Load remaining installed models in the background
            others = [m["name"] for m in local if m["name"] != default_name and m["name"] not in running_names]
            for name in others:
                try:
                    print(f"[startup] auto-loading '{name}'…", flush=True)
                    await ollama.load(name)
                    print(f"[startup] '{name}' loaded.", flush=True)
                except Exception as exc:
                    print(f"[startup] could not load '{name}': {exc}", flush=True)
    except Exception as exc:
        print(f"[startup] could not auto-load Ollama models: {exc}", flush=True)


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=7860, title="Shokan", storage_secret=SESSION_SECRET)
