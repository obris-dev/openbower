"""One processor per node kind: the service that knows how a kind's
runs come to exist for a set of rows AND how one of them executes. The
config classes in lists/nodes are what a node IS at rest; a processor
is what the kind DOES. Every walker that queues runs (the enqueue job,
autofill, the advance) asks the factory for the processor of the node
it holds and hands it the rows; every dispatcher that executes runs
(the node-run consumer, the deferred flush) asks the same factory and
hands it the claimed run or the node. The per-kind judgement and the
per-kind execution live in one place per kind, and the walkers and
dispatchers know nothing about kinds.

Each processor module registers itself at its own bottom; the roster
is ListsConfig.ready()'s walk of this package, the node-kind pattern.
"""

from .base import BatchTally, FillMode, FillScope, NodeProcessor, RunOutcome
from .factory import UnknownProcessor, processor_for

__all__ = [
    "BatchTally",
    "FillMode",
    "FillScope",
    "NodeProcessor",
    "RunOutcome",
    "UnknownProcessor",
    "processor_for",
]
