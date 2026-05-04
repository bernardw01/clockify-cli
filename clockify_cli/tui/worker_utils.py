"""Helpers for Textual workers."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.dom import DOMNode


async def cancel_and_wait_running_workers(node: DOMNode) -> None:
    """Cancel workers bound to ``node`` and await completion.

    Call before popping a screen so its async workers (e.g. sync) finish their
    ``finally`` blocks while child widgets still exist. Otherwise unmount can
    tear down the tree while a worker is still unwinding.
    """
    from textual.worker import WorkerCancelled, WorkerFailed

    app = node.app
    pending = [w for w in app.workers if w.node is node and w.is_running]
    for worker in pending:
        worker.cancel()
    for worker in pending:
        try:
            await worker.wait()
        except (WorkerCancelled, WorkerFailed):
            pass
