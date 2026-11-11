"""Price levels taught in class: pivots (traditional + Camarilla) and Fibonacci."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Camarilla:
    """Camarilla pivot levels from one period's high, low and close (06/12 & 07/12 notes).

    Intraday uses the previous DAY, swing the previous MONTH, positional the previous YEAR.
    """
    h: float
    l: float
    c: float

    @property
    def range(self) -> float:
        return self.h - self.l

    @property
    def pivot(self) -> float:  # "average point" = (H + L + C) / 3
        return (self.h + self.l + self.c) / 3

    def r(self, n: int) -> float:
        if n == 5:  # R5 = R4 + 1.168 × (R4 − R3)
            return self.r(4) + 1.168 * (self.r(4) - self.r(3))
        return self.c + self.range * 1.1 / {1: 12, 2: 6, 3: 4, 4: 2}[n]

    def s(self, n: int) -> float:
        if n == 5:
            return self.s(4) - 1.168 * (self.s(3) - self.s(4))
        return self.c - self.range * 1.1 / {1: 12, 2: 6, 3: 4, 4: 2}[n]

    def as_dict(self) -> dict:
        out = {"Pivot": self.pivot}
        for n in (5, 4, 3, 2, 1):
            out[f"R{n}"] = self.r(n)
        for n in (1, 2, 3, 4, 5):
            out[f"S{n}"] = self.s(n)
        return out


@dataclass(frozen=True)
class Traditional:
    """Traditional (floor) pivots — longer targets, used for positional trades."""
    h: float
    l: float
    c: float

    def as_dict(self) -> dict:
        p = (self.h + self.l + self.c) / 3
        rng = self.h - self.l
        return {
            "Pivot": p,
            "R1": 2 * p - self.l, "S1": 2 * p - self.h,
            "R2": p + rng, "S2": p - rng,
            "R3": self.h + 2 * (p - self.l), "S3": self.l - 2 * (self.h - p),
        }


RETRACEMENTS = (0.382, 0.5, 0.618, 0.786)  # 13/12 notes
EXTENSIONS = (0.618, 1.0, 1.618)


def fib_retracements(swing_low: float, swing_high: float) -> dict:
    """Support levels inside an up-move (buy side only)."""
    rng = swing_high - swing_low
    return {r: swing_high - rng * r for r in RETRACEMENTS}


def fib_extensions(swing_low: float, swing_high: float, pullback_low: float) -> dict:
    """Target levels above the pullback, measured with the size of the prior up-move."""
    rng = swing_high - swing_low
    return {e: pullback_low + rng * e for e in EXTENSIONS}
