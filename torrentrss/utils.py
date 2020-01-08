from __future__ import annotations

import os
import sys
import asyncio
import subprocess
from os import PathLike
from functools import wraps, partial
from concurrent.futures import Executor
from asyncio.events import AbstractEventLoop
from typing import (
    overload,
    Optional,
    Dict,
    Any,
    TypeVar,
    Callable,
    Coroutine
)

from aiofile import AIOFile

from .constants import NAME, WINDOWS


Json = Dict[str, Any]


T = TypeVar('T')
R = TypeVar('R')


@overload
def wrap_for_asyncio(func: Callable[[T], R]) -> Callable[[T], Coroutine[Any, Any, R]]:
    ...


@overload
def wrap_for_asyncio(func: Callable[..., R]) -> Callable[..., Coroutine[Any, Any, R]]:
    ...


def wrap_for_asyncio(func):
    @wraps(func)
    async def run(
        *args,
        loop: Optional[AbstractEventLoop] = None,
        executor: Optional[Executor] = None,
        **kwargs
    ):
        if loop is None:
            loop = asyncio.get_event_loop()
        partial_func = partial(func, *args, **kwargs)
        return loop.run_in_executor(executor, partial_func)

    return run


run_subprocess = wrap_for_asyncio(subprocess.run)


async def show_exception_notification(exception: Exception) -> None:
    try:
        await run_subprocess(
            args=[
                'notify-send',
                '--app-name',
                NAME,
                NAME,
                f'An exception of type {exception.__class__.__name__} occurred.'
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        pass


async def read_text(path: PathLike) -> str:
    async with AIOFile(path) as file:
        return await file.read()


async def write_text(path: PathLike, text: str) -> None:
    async with AIOFile(path, mode='w') as file:
        await file.write(text)
        await file.fsync()


async def open_with_default_application(url: str) -> None:
    if WINDOWS:
        # startfile cannot be waited upon at all, so just complete immediately
        os.startfile(url)
        return
    await run_subprocess(
        args=['open' if sys.platform == 'darwin' else 'xdg-open', url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
