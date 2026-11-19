"""Safe dashboard boundary failures with no request or upstream payload attached."""


class BoundaryError(Exception):
    def __init__(self, status: int, code: str, retry_after: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_after = retry_after
