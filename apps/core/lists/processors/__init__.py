"""One processor per node kind: the service that knows how a kind's
runs come to exist for a set of rows. The config classes in lists/nodes
are what a node IS at rest; a processor is what the kind DOES. Every
walker that materializes runs (the webhook backfill job, and the fill
and autofill admissions as they move onto the same shape) asks the
factory for the processor of the node it holds and hands it the rows,
so the per-kind judgement lives in one place per kind and the walkers
know nothing about kinds.

Each processor module registers itself at its own bottom; the roster
is ListsConfig.ready()'s walk of this package, the node-kind pattern.
"""

from .base import NodeProcessor
from .factory import UnknownProcessor, processor_for

__all__ = ["NodeProcessor", "UnknownProcessor", "processor_for"]
