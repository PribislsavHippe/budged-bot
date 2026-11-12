"""Time windows and preferences for a person on a specific roster day."""

from dataclasses import dataclass


def clock(hour):
    minutes = round(hour * 60)
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def cell_hour(hour):
    return str(int(hour)) if int(hour) == hour else clock(hour)


@dataclass(frozen=True)
class Rule:
    earliest: float = 6
    latest: float = 23
    preferred_start: float | None = None
    preferred_end: float | None = None

    def permits(self, start, end):
        return self.earliest <= start < end <= self.latest

    def misses(self, start, end):
        return self.preferred_start is not None and (
            start != self.preferred_start or self.preferred_end is not None and end != self.preferred_end)

    def shift(self, slot, opener, closing_start, standard_end):
        """Best available interval for a slot, keeping its opening/closing role if possible."""
        first, last = max(self.earliest, opener), self.latest
        if first >= last:
            return None
        starts = {first, slot, closing_start, self.preferred_start}
        options = []
        for start in starts - {None}:
            if not first <= start < last:
                continue
            end = min(standard_end(start), last)
            ends = {end}
            if self.preferred_end is not None and start < self.preferred_end <= end:
                ends.add(self.preferred_end)
            for end in ends:
                same_role = ((slot == opener) == (start == opener)
                             and (slot >= closing_start) == (start >= closing_start and end == 23))
                score = (not same_role, self.misses(start, end), abs(start - slot), start - end, start)
                options.append((score, start, end))
        _, start, end = min(options)
        return start if end == standard_end(start) else (start, end)
