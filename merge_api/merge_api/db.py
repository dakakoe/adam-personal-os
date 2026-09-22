from __future__ import annotations

import contextvars

import asyncpg

# Per-request actor for the fin_audit triggers. The role-guard middleware sets
# this to 'owner' / 'member'; background workers leave it at the default. The pool
# `setup` hook below copies it onto every checked-out connection's app.actor GUC,
# which `memory._fin_audit()` reads via current_setting('app.actor').
current_actor: contextvars.ContextVar[str] = contextvars.ContextVar("current_actor", default="system")


async def _apply_actor(conn: asyncpg.Connection) -> None:
    # runs on every pool.acquire(), inside the acquiring task's context, so the
    # contextvar value is the current request's actor; re-stamped each checkout.
    await conn.execute("SELECT set_config('app.actor', $1, false)", current_actor.get())


async def connect(db_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        db_url, min_size=2, max_size=8, statement_cache_size=0, setup=_apply_actor,
    )
