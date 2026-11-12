"""The shape of one MSP message's payloads."""

from dataclasses import dataclass, field

import construct as c


@dataclass(frozen=True)
class MspLayout:
    """Payload fields of one MSP message: what a request carries and what the reply contains."""

    request: c.Struct = field(default_factory=c.Struct)
    reply: c.Struct = field(default_factory=c.Struct)
