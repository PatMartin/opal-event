"""``/api/v1/events`` and ``/api/v1/topics``: publish (ZeroMQ handoff) and inspect stored history."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from opal_event.auth import require_auth
from opal_event.deps import get_config, get_service
from opal_event.errors import NotFoundError, PayloadTooLargeError, ServiceUnavailableError
from opal_event.models import Event, EventInput, ListEvents, PublishReceipt, TopicList
from opal_event.normalization import normalize_event
from opal_event.opal_context import get_opal_context
from opal_event.pagination import decode_scoped_cursor, encode_scoped_cursor
from opal_event.service import EventService
from opal_event.zeromq_broker import PublisherQueueFullError

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/api/v1/events/", status_code=202, response_model=PublishReceipt)
def publish_event(
    event_input: EventInput,
    request: Request,
    service: Annotated[EventService, Depends(get_service)],
    config=Depends(get_config),
):
    if not service.is_ready():
        raise ServiceUnavailableError("ZeroMQ broker is not running")
    envelope = normalize_event(event_input)
    wire_size = len(envelope["topic"].encode("utf-8")) + len(json.dumps(envelope).encode("utf-8"))
    if wire_size > config.settings.zeromq.max_event_bytes:
        raise PayloadTooLargeError()

    # Suite standard v1.2: "Propagate them into derived ZeroMQ envelope metadata." Additive-only
    # optional keys on the outbound two-frame envelope (zeromq_protocol.md permits extra optional
    # keys beyond id/source/timestamp); never added to the stored/REST Event shape, which forbids
    # additional properties. Best-effort: dropped if it would push the envelope over the byte cap.
    context = get_opal_context(request)
    enrichment = {
        "session_id": context.session_id,
        "transaction_id": context.transaction_id,
        "causation_id": context.causation_id,
        "opal_session_id": context.session_id,
        "opal_transaction_id": context.transaction_id,
        "opal_causation_id": context.causation_id,
    }
    enrichment = {k: v for k, v in enrichment.items() if v is not None}
    if enrichment:
        enriched_envelope = {**envelope, **enrichment}
        enriched_size = len(envelope["topic"].encode("utf-8")) + len(json.dumps(enriched_envelope).encode("utf-8"))
        if enriched_size <= config.settings.zeromq.max_event_bytes:
            envelope = enriched_envelope

    try:
        service.publish(envelope)
    except PublisherQueueFullError as exc:
        raise ServiceUnavailableError("Publisher queue is full") from exc
    return PublishReceipt(id=envelope["id"], status="accepted", published_at=envelope["timestamp"])


@router.get("/api/v1/events/", response_model=ListEvents)
def list_events(
    service: Annotated[EventService, Depends(get_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    topic: str | None = None,
    source: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
):
    filters = {"topic": topic, "source": source, "from": from_, "to": to}
    inner_cursor = decode_scoped_cursor(cursor, filters) if cursor else None
    items, next_inner_cursor = service.db.list_events(
        cursor=inner_cursor, limit=limit, topic=topic, source=source, time_from=from_, time_to=to
    )
    next_cursor = encode_scoped_cursor(next_inner_cursor, filters) if next_inner_cursor else None
    return ListEvents(items=[Event.model_validate(item) for item in items], next_cursor=next_cursor)


@router.get("/api/v1/events/{event_id}", response_model=Event)
def get_event(event_id: str, service: Annotated[EventService, Depends(get_service)]):
    event = service.db.get_event(event_id)
    if event is None:
        raise NotFoundError(f"event '{event_id}' not found")
    return Event.model_validate(event)


@router.delete("/api/v1/events/{event_id}", status_code=204)
def delete_event(event_id: str, service: Annotated[EventService, Depends(get_service)]):
    if not service.db.delete_event(event_id):
        raise NotFoundError(f"event '{event_id}' not found")
    return Response(status_code=204)


@router.get("/api/v1/topics/", response_model=TopicList)
def list_topics(service: Annotated[EventService, Depends(get_service)], response: Response, config=Depends(get_config)):
    response.headers["ETag"] = config.revision
    return TopicList(items=service.db.list_topics())


@router.delete("/api/v1/events/", status_code=204)
def clear_events(service: Annotated[EventService, Depends(get_service)]):
    service.db.clear_events()
    return Response(status_code=204)
