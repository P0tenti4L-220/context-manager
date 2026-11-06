"""Deeply immutable snapshots at component boundaries.

Components build local mutable records. Public entry points publish an isolated
snapshot; callers can obtain a fresh mutable JSON projection with dump_object.
Mappings use composition rather than dict inheritance, so even dict's unbound
mutation methods cannot bypass the boundary. dump_object yields JSON containers.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from functools import wraps
from types import MappingProxyType


def _immutable(*args, **kwargs):
    raise TypeError("published view is read-only")


class FrozenDict(Mapping):
    __slots__ = ("_data",)

    def __init__(self, *args, **kwargs):
        if hasattr(self, "_data"):
            raise TypeError("published view is read-only")
        object.__setattr__(self, "_data", MappingProxyType(dict(*args, **kwargs)))

    __setattr__ = __delattr__ = _immutable

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"FrozenDict({dict(self._data)!r})"

    def __or__(self, other):
        return dict(self) | dict(other) if isinstance(other, Mapping) else NotImplemented

    def __ror__(self, other):
        return dict(other) | dict(self) if isinstance(other, Mapping) else NotImplemented

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class FrozenList(tuple):
    __slots__ = ()

    def __new__(cls, items=()):
        return tuple.__new__(cls, items)

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class ReadonlyRecord:
    """A builder record that is sealed before it leaves its owning component."""

    _is_frozen = False

    def __setattr__(self, name, value):
        if self._is_frozen:
            raise FrozenInstanceError("published view is read-only")
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if self._is_frozen:
            raise FrozenInstanceError("published view is read-only")
        object.__delattr__(self, name)

    def freeze(self):
        if not self._is_frozen:
            for field in fields(self):
                object.__setattr__(self, field.name, freeze(getattr(self, field.name)))
            object.__setattr__(self, "_is_frozen", True)
        return self

    def __deepcopy__(self, memo):
        if self._is_frozen:
            return self
        return type(self)(**{f.name: copy.deepcopy(getattr(self, f.name), memo) for f in fields(self) if f.init})


def freeze(value):
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, ReadonlyRecord):
        return value.freeze()
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(freeze(item) for item in sorted(value))
    if is_dataclass(value):
        return replace(value, **{f.name: freeze(getattr(value, f.name)) for f in fields(value) if f.init})
    return value


def readonly(function):
    @wraps(function)
    def publish(*args, **kwargs):
        return freeze(function(*args, **kwargs))
    return publish
