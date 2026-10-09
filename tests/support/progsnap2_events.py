"""
Spec-driven ProgSnap2 event factory. Fills in every column the spec requires
for an event type, so tests only spell out what they care about. Doesn't
depend on provena; a candidate for the toolbox (docs/tasks/testing.md,
section 4).
"""

import datetime as dt
import itertools
import uuid
from typing import Any

from progsnap2.spec.datatypes import PS2Datatype
from progsnap2.spec.spec_definition import ProgSnap2Spec, Requirement

_BASE_TIME = dt.datetime(2026, 1, 1, 12, 0, 0)

_order = itertools.count(1)

# Defaults by column name, for columns that are filled in. Callables are
# called per event.
_NAMED_DEFAULTS = {
    "EventID": lambda: str(uuid.uuid4()),
    "ToolInstances": "tests",
    "SubjectID": "student1",
    "SessionID": "session1",
    "CodeStateSection": "proj/main.py",
    "DestinationCodeStateSection": "proj/renamed.py",
    "Code": "print('hello')\n",
    "ProjectID": "project1",
}

# Not required by the spec, but always sent by the VS Code extension, and
# most queries filter or sort on them.
_ALWAYS_INCLUDED = ["SubjectID", "SessionID", "Order", "ClientTimestamp"]


def timestamp(order: int) -> str:
    """A client timestamp `order` seconds after a fixed base time, so events
    sort the same way by timestamp as by Order."""
    return (_BASE_TIME + dt.timedelta(seconds=order)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "+0000"


def make_event(spec: ProgSnap2Spec, event_type: str, **overrides: Any) -> dict[str, Any]:
    """
    Returns one main-table event as a dict, ready to post to /events or pass
    to a writer. Overrides replace defaults; an override of None removes the
    column entirely. Order counts up across calls, and ClientTimestamp
    follows Order unless given.
    """
    type_spec = spec.main_table.get_event_type(event_type)
    if type_spec is None:
        raise ValueError(f"Unknown event type {event_type!r}")

    names = [column.name for column in spec.main_table.columns if column.requirement == Requirement.Required]
    names += type_spec.required_columns or []
    names += _ALWAYS_INCLUDED

    order = overrides.get("Order")
    if order is None:
        order = next(_order)

    event: dict[str, Any] = {}
    for name in dict.fromkeys(names):  # dedupe, keep order
        if name == "EventType":
            event[name] = event_type
        elif name == "Order":
            event[name] = order
        elif name == "ClientTimestamp":
            event[name] = timestamp(order)
        else:
            event[name] = _default_value(spec, name)

    for name, value in overrides.items():
        if value is None:
            event.pop(name, None)
        else:
            event[name] = value
    return event


def _default_value(spec: ProgSnap2Spec, name: str) -> Any:
    if name in _NAMED_DEFAULTS:
        default = _NAMED_DEFAULTS[name]
        return default() if callable(default) else default

    column = spec.main_table.get_column(name)
    datatype = column.datatype if column else PS2Datatype.String
    if datatype == PS2Datatype.Enum:
        enum_type = next((e for e in spec.enum_types if e.name == name), None)
        return enum_type.values[0].name if enum_type else f"test-{name}"
    if datatype == PS2Datatype.Integer:
        return 1
    if datatype == PS2Datatype.Real:
        return 1.0
    if datatype == PS2Datatype.Boolean:
        return False
    if datatype == PS2Datatype.Timestamp:
        return timestamp(0)
    return f"test-{name}"
