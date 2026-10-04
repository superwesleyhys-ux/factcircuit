"""Conservative per-question Astra/Circuit selector for binary Brier forecasts.

The judgment interval must be elicited from decision-time evidence without
showing the judge either arm's probability or any outcome. This module only
checks the arithmetic; callers retain and audit that blind judgment separately.
"""

from dataclasses import dataclass
import math


def _probability(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'{name} must be a finite probability in [0, 1]')
    return float(value)


@dataclass(frozen=True)
class Route:
    arm: str
    probability: float
    midpoint: float
    reason: str


@dataclass(frozen=True)
class PartialFallback:
    probability: float
    circuit_weight: float
    reason: str


def partial_fallback(astra_probability, circuit_probability,
                     judgment_low=None, judgment_high=None):
    """Use as much of Circuit as the blind judgment interval robustly supports.

    Among forecasts between Astra and Circuit, minimize expected Brier loss at
    the adverse end of the subjective interval. This produces a partial blend
    when the adverse end lies strictly between the two forecasts. It is a
    conditional guarantee, not a guarantee that the judgment is calibrated.
    """
    a = _probability(astra_probability, 'astra_probability')
    c = _probability(circuit_probability, 'circuit_probability')
    if (judgment_low is None) != (judgment_high is None):
        raise ValueError('judgment bounds must both be present or both be absent')
    if judgment_low is None:
        return PartialFallback(a, 0.0, 'no_blind_judgment')
    low = _probability(judgment_low, 'judgment_low')
    high = _probability(judgment_high, 'judgment_high')
    if low > high:
        raise ValueError('judgment bounds reversed')
    if c == a:
        return PartialFallback(a, 0.0, 'identical_forecasts')
    adverse = low if c > a else high
    blended = min(max(adverse, min(a, c)), max(a, c))
    weight = (blended-a)/(c-a)
    if weight == 0:
        reason = 'astra_supported_at_adverse_bound'
    elif weight == 1:
        reason = 'full_circuit_supported_at_adverse_bound'
    else:
        reason = 'partial_circuit_supported_at_adverse_bound'
    return PartialFallback(blended, weight, reason)


def choose(astra_probability, circuit_probability, judgment_low=None, judgment_high=None):
    """Choose Circuit only if its expected Brier loss is lower across the interval.

    For binary outcome Y, the Circuit-minus-Astra loss is
    (pC-pA)*(pC+pA-2*Y). With an independent event estimate q, Circuit wins
    when this expression is negative after substituting q for Y. An interval
    represents uncertainty about q; if it straddles the boundary, use Astra.
    Missing independent judgment also uses Astra. Ties use Astra.
    """
    a = _probability(astra_probability, 'astra_probability')
    c = _probability(circuit_probability, 'circuit_probability')
    midpoint = (a+c)/2
    if (judgment_low is None) != (judgment_high is None):
        raise ValueError('judgment bounds must both be present or both be absent')
    if judgment_low is not None:
        low = _probability(judgment_low, 'judgment_low')
        high = _probability(judgment_high, 'judgment_high')
        if low > high:
            raise ValueError('judgment bounds reversed')
    if c == a:
        return Route('Astra', a, midpoint, 'identical_forecasts')
    if judgment_low is None:
        return Route('Astra', a, midpoint, 'no_blind_judgment')
    if c > a and low > midpoint:
        return Route('Circuit', c, midpoint, 'higher_forecast_supported_across_interval')
    if c < a and high < midpoint:
        return Route('Circuit', c, midpoint, 'lower_forecast_supported_across_interval')
    return Route('Astra', a, midpoint, 'uncertain_or_astra_supported')
