"""Provenance-aware agent memory (ADR 0007)."""

from headofcontext.memory.inmemory import InMemoryMemoryAdapter
from headofcontext.memory.ledger import InMemoryLedger, LedgerUnavailable, PostgresLedger
from headofcontext.memory.model import (
    Candidate,
    Memory,
    MemoryAdapter,
    MemoryRecord,
    ProvenanceLedger,
    ResourcePermits,
)
from headofcontext.memory.postgres import PostgresMemoryAdapter
from headofcontext.memory.service import DEFAULT_NAMESPACE, MemoryService

__all__ = [
    "DEFAULT_NAMESPACE",
    "Candidate",
    "InMemoryLedger",
    "InMemoryMemoryAdapter",
    "LedgerUnavailable",
    "Memory",
    "MemoryAdapter",
    "MemoryRecord",
    "MemoryService",
    "PostgresLedger",
    "PostgresMemoryAdapter",
    "ProvenanceLedger",
    "ResourcePermits",
]
