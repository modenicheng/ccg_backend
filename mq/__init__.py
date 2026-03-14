"""Message Queue (MQ) module for the CCG backend using Huey for task scheduling."""

from __future__ import annotations

from . import tasks

__all__ = ["tasks"]
