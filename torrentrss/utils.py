from __future__ import annotations

import shutil
from os import PathLike
from typing import Dict, Any
from subprocess import Popen, DEVNULL

from aiofile import AIOFile

from .constants import NAME


Json = Dict[str, Any]


def show_exception_notification(exception: Exception) -> None:
    if shutil.which('notify-send') is None:
        return
    text = f'An exception of type {exception.__class__.__name__} occurred.'
    Popen(
        args=['notify-send', '--app-name', NAME, NAME, text],
        stdout=DEVNULL,
        stderr=DEVNULL
    )


async def read_text(path: PathLike) -> str:
    async with AIOFile(path) as file:
        return await file.read()


async def write_text(path: PathLike, text: str) -> None:
    async with AIOFile(path, mode='w') as file:
        await file.write(text)
        await file.fsync()
