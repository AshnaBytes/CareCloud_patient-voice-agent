"""One exception type for expected API failures; handlers in main.py turn it into the JSON envelope."""
from typing import Any, Optional


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[Any] = None):
        self.status, self.code, self.message, self.details = status, code, message, details
        super().__init__(message)
