from unittest.mock import patch, ANY

import pytest

from ..command import Command


@pytest.mark.asyncio
async def test_arguments() -> None:
    command = Command(['command', '$URL', '--option'])

    with patch('torrentrss.command.Popen') as mock:
        await command('http://test.com/test.torrent')
        mock.assert_called_once_with(
            args=['command', 'http://test.com/test.torrent', '--option'],
            startupinfo=ANY
        )


@pytest.mark.asyncio
async def test_no_arguments() -> None:
    command = Command()

    with patch.object(Command, 'launch_with_default_application') as mock:
        await command('http://test.com/test.torrent')
        mock.assert_called_once_with('http://test.com/test.torrent')
