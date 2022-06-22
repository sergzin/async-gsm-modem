from pydantic import BaseModel
from typing import Optional


class VoiceCall(BaseModel):
    index: int
    direction: str
    state: str
    mode: str
    conf: bool
    number: Optional[bytes]
    type: Optional[str]
