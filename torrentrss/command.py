from __future__ import annotations

import os
import re
import sys
from typing import Optional, List, Iterator, Match, cast
from subprocess import Popen, DEVNULL, STARTUPINFO, STARTF_USESHOWWINDOW

from .logging import logger
from .constants import WINDOWS, COMMAND_URL_ARGUMENT


class Command:
    arguments: Optional[List[str]]

    def __init__(self, arguments: Optional[List[str]] = None) -> None:
        self.arguments = arguments

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(arguments={self.arguments})'

    @staticmethod
    def launch_with_default_application(url: str) -> None:
        if WINDOWS:
            os.startfile(url)
            return
        Popen(
            args=['open' if sys.platform == 'darwin' else 'xdg-open', url],
            stdout=DEVNULL,
            stderr=DEVNULL
        )

    def subbed_arguments(self, url: str) -> Iterator[str]:
        # The repl parameter here is a function which at first looks like it
        # could just be a string, but it actually needs to be a function or
        # else escapes in the string would be processed, leading to problems
        # when dealing with file paths, for example.
        # See: https://docs.python.org/3/library/re.html#re.sub
        #      https://stackoverflow.com/a/16291763/3289208
        def replacer(_: Match) -> str:
            return url
        for argument in cast(List[str], self.arguments):
            yield re.sub(
                pattern=re.escape(COMMAND_URL_ARGUMENT),
                repl=replacer,
                string=argument
            )

    async def __call__(self, url: str) -> Optional[Popen]:
        if self.arguments is not None:
            startupinfo: Optional[STARTUPINFO]
            if WINDOWS:
                startupinfo = STARTUPINFO()
                startupinfo.dwFlags = STARTF_USESHOWWINDOW
            else:
                startupinfo = None

            arguments = list(self.subbed_arguments(url))
            logger.info(
                f'Launching subprocess with arguments {arguments}'
            )
            return Popen(
                args=arguments,
                startupinfo=startupinfo
            )

        logger.info(f'Launching {url!r} with default program')
        self.launch_with_default_application(url)
        return None
