from __future__ import annotations

from typing import Optional, Match, Any


class EpisodeNumber:
    series: Optional[int]
    episode: Optional[int]

    def __init__(self, series: Optional[int], episode: Optional[int]) -> None:
        self.series = series
        self.episode = episode

    @classmethod
    def from_regex_match(cls, match: Match) -> EpisodeNumber:
        groups = match.groupdict()
        return cls(
            series=int(groups['series']) if 'series' in groups else None,
            episode=int(groups['episode'])
        )

    def __gt__(self, other: EpisodeNumber) -> bool:
        if self.episode is None:
            return False
        if other.episode is None:
            return True
        if self.series is not None \
                and other.series is not None \
                and self.series != other.series:
            return self.series > other.series
        return self.episode > other.episode

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(series={self.series}, episode={self.episode})'

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, EpisodeNumber):
            return NotImplemented
        return self.series == other.series and self.episode == other.episode
