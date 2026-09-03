"""The answerer, as a package: `record` holds the facts types
(CallRecord, Answer), `errors` the outward exception vocabulary,
`schema` the answer schema's construct/deconstruct pair, `validators`
the framework-seam chain (verify then ground), and `answerer` the
CellAnswerer that runs the whole call. The public surface is
re-exported here."""

from .answerer import CellAnswerer as CellAnswerer
from .errors import (
    AgentError as AgentError,
)
from .errors import (
    AgentOverloaded as AgentOverloaded,
)
from .errors import (
    AgentRateLimited as AgentRateLimited,
)
from .errors import (
    AgentResponseInvalid as AgentResponseInvalid,
)
from .errors import (
    AgentTimeout as AgentTimeout,
)
from .errors import (
    AgentUnableToRespond as AgentUnableToRespond,
)
from .errors import (
    AgentUnreachable as AgentUnreachable,
)
from .errors import (
    EmptyRender as EmptyRender,
)
from .errors import (
    NoAvailableTools as NoAvailableTools,
)
from .record import EMPTY_RECORD as EMPTY_RECORD
from .record import Answer as Answer
from .record import CallRecord as CallRecord
from .schema import companions as companions
from .schema import reserved_output_key as reserved_output_key
