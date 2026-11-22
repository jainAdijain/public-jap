"""What an inbound-email service posts to JAP (IDEA-16, route B)."""

from pydantic import BaseModel, Field


class InboundEmail(BaseModel):
    """One delivered message. Only the recipient and the text are wanted; whatever else
    the service sends is ignored rather than stored."""

    to: str = Field(max_length=320)  # jap+<token>@...
    text: str = Field(max_length=100_000)
    # How many characters the code has. Greenhouse's is 8; the form says so and
    # submit_form passes it on, so this is only the default for a service that cannot.
    length: int = Field(default=8, ge=4, le=16)


class InboundTaken(BaseModel):
    """Whether a code was found and given to an application that was waiting."""

    taken: bool
