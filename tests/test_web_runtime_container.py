from __future__ import annotations

import threading
from collections import deque

from invest_evolution.interfaces.web.runtime import (
    WebRuntimeEphemeralState,
    WebRuntimeStateContainer,
)


def test_runtime_container_updates_embedded_runtime_and_facade_state():
    state = WebRuntimeEphemeralState(event_history_limit=8, event_buffer_limit=4)
    container = WebRuntimeStateContainer(ephemeral_state=state)
    runtime = object()
    loop = object()
    facade = object()
    thread = threading.Thread(target=lambda: None)

    container.bind_runtime(runtime=runtime, loop=loop)
    container.set_runtime_facade_override(facade)
    container.set_runtime_shutdown_registered(True)
    container.set_event_dispatcher_thread(thread)

    assert container.runtime is runtime
    assert container.loop is loop
    assert container.runtime_facade_override is facade
    assert container.runtime_shutdown_registered is True
    assert container.event_dispatcher_thread is thread


def test_runtime_container_keeps_ephemeral_state_identity():
    state = WebRuntimeEphemeralState(event_history_limit=2, event_buffer_limit=2)
    state.rate_limit_events = {("127.0.0.1", "read", "/api/status"): deque([1.0])}
    container = WebRuntimeStateContainer(ephemeral_state=state)

    assert container.ephemeral_state is state
    container.ephemeral_state.reset()
    assert container.ephemeral_state.rate_limit_events == {}


def test_runtime_container_sync_from_compat_aliases_updates_fields():
    state = WebRuntimeEphemeralState(event_history_limit=2, event_buffer_limit=2)
    container = WebRuntimeStateContainer(ephemeral_state=state)
    runtime = object()
    loop = object()
    facade = object()
    thread = threading.Thread(target=lambda: None)

    changed = container.sync_from_compat_aliases(
        runtime=runtime,
        loop=loop,
        runtime_facade_override=facade,
        runtime_shutdown_registered=True,
        event_dispatcher_thread=thread,
    )

    assert changed is True
    assert container.runtime is runtime
    assert container.loop is loop
    assert container.runtime_facade_override is facade
    assert container.runtime_shutdown_registered is True
    assert container.event_dispatcher_thread is thread
