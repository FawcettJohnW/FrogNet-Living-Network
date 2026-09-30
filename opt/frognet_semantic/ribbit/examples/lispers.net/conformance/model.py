from dataclasses import dataclass, field
from typing import Any, Protocol

class LispAdapter(Protocol):
    def capabilities(self) -> set[str]: ...
    def call(self, operation: str, **kwargs: Any) -> Any: ...

@dataclass(frozen=True)
class Case:
    id: str
    operation: str
    args: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None
    destructive: bool = False
