from typing import Any
from pathlib import Path
from asyncio import Future


def task_mock(value: Any = None) -> Future:
    future = Future()
    future.set_result(value)
    return future


def local_path(name: str) -> Path:
    return Path(__file__).with_name(name)
