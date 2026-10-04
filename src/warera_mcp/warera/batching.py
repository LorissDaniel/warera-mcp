"""Bounded, credential-isolated collection of concurrent reads; no result cache."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from warera_mcp.warera.errors import WareraError, WareraOverloaded


@dataclass(slots=True)
class BatchCall:
    procedure: str
    payload: str
    headers: dict[str, str] = field(repr=False)
    future: asyncio.Future[object] = field(repr=False)


Dispatch = Callable[[list[BatchCall]], Awaitable[list[object | WareraError]]]
Fits = Callable[[list[BatchCall]], bool]


class ReadBatcher:
    """Each caller owns a future; cancelling it cannot cancel another caller.

    Credentials exist only in pending/active calls, never in logs or a persistent
    pool. Capacity includes active callers so slow upstreams cannot grow memory
    without bound. Timers use the event loop, independently of retry sleepers.
    """

    def __init__(
        self, *, window: float, max_size: int, capacity: int, dispatch: Dispatch, fits: Fits
    ) -> None:
        self._window = window
        self._max_size = max_size
        self._capacity = capacity
        self._dispatch = dispatch
        self._fits = fits
        self._groups: dict[tuple[tuple[str, str], ...], list[BatchCall]] = {}
        self._timers: dict[tuple[tuple[str, str], ...], asyncio.TimerHandle] = {}
        self._tasks: dict[asyncio.Task[None], list[BatchCall]] = {}
        self._count = 0
        self._closed = False

    async def submit(self, procedure: str, payload: str, headers: Mapping[str, str]) -> object:
        if self._closed or self._count >= self._capacity:
            raise WareraOverloaded()
        self._count += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        call = BatchCall(procedure, payload, dict(headers), future)
        key = tuple(sorted(headers.items()))
        group = self._groups.setdefault(key, [])
        if group and (len(group) >= self._max_size or not self._fits([*group, call])):
            self._flush(key)
            group = self._groups.setdefault(key, [])
        group.append(call)
        if key not in self._timers:
            self._timers[key] = loop.call_later(self._window, self._flush, key)
        if len(group) >= self._max_size:
            self._flush(key)
        try:
            return await future
        finally:
            self._count -= 1
            # Release cancelled callers' credentials promptly during collection.
            if future.cancelled() and key in self._groups:
                remaining = [item for item in self._groups[key] if not item.future.done()]
                if remaining:
                    self._groups[key] = remaining
                else:
                    self._groups.pop(key)
                    self._timers.pop(key).cancel()

    def _flush(self, key: tuple[tuple[str, str], ...]) -> None:
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        calls = [call for call in self._groups.pop(key, []) if not call.future.done()]
        if not calls:
            return
        task = asyncio.create_task(self._execute(calls))
        self._tasks[task] = calls
        task.add_done_callback(lambda done: self._tasks.pop(done, None))

        def cancel_if_unused(_: asyncio.Future[object]) -> None:
            if all(call.future.done() for call in calls):
                task.cancel()

        for call in calls:
            call.future.add_done_callback(cancel_if_unused)

    async def _execute(self, calls: list[BatchCall]) -> None:
        # Canonical payloads allow equivalent dictionaries to share one read.
        unique: dict[tuple[str, str], BatchCall] = {}
        for call in calls:
            unique.setdefault((call.procedure, call.payload), call)
        try:
            outcomes = await self._dispatch(list(unique.values()))
            if len(outcomes) != len(unique):
                raise RuntimeError("batch dispatcher returned an incorrect result count")
            by_key = dict(zip(unique, outcomes, strict=True))
            for call in calls:
                if call.future.done():
                    continue
                outcome = by_key[(call.procedure, call.payload)]
                if isinstance(outcome, WareraError):
                    call.future.set_exception(outcome)
                else:
                    call.future.set_result(outcome)
        except asyncio.CancelledError:
            for call in calls:
                call.future.cancel()
            raise
        except Exception as error:
            for call in calls:
                if not call.future.done():
                    call.future.set_exception(error)

    async def aclose(self) -> None:
        self._closed = True
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        for group in self._groups.values():
            for call in group:
                call.future.cancel()
        self._groups.clear()
        tasks = list(self._tasks)
        for task in tasks:
            # A just-created task may not have entered _execute's try block yet.
            for call in self._tasks[task]:
                call.future.cancel()
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
