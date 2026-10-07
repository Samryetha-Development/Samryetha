from collections.abc import Callable, Sequence

class BackgroundScheduler:
    def __init__(self, *, daemon: bool = ...) -> None: ...
    def add_job(
        self,
        func: Callable[..., object],
        *,
        trigger: object,
        args: Sequence[object] = ...,
        id: str,
        replace_existing: bool,
    ) -> object: ...
    def start(self) -> None: ...
    def shutdown(self, wait: bool = ...) -> None: ...
