from __future__ import annotations


class TorrentRSSError(Exception):
    pass


class ConfigError(TorrentRSSError):
    pass


class FeedError(TorrentRSSError):
    pass
