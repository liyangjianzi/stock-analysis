"""Lifespans: when each person dies, from Canadian cohort life tables.

The base table is Statistics Canada's complete life table for Alberta, 2021/2023
(three-year estimate, centre year 2022), death probability ``qx`` by single age
0-110 and sex. Death rates then fall every year at the 32nd CPP Actuarial
Report's ultimate improvement rates (1.0% a year under 90, 0.6% at 90-94, 0.2% at
95+, for both sexes). The report reaches those rates in 2039; applying them from
2022 on slightly understates improvement before then.

A **death age** is the age reached on the January 1 after the last year lived:
dying during the year at age ``a`` gives ``a + 1``. The engine counts a person
alive in a year while their age is below it.

Pure: no I/O.
"""
from __future__ import annotations

import numpy as np

from .rules import Rule

OMEGA = 111                 # largest death age: the table's last row is "110 and over", q = 1
TABLE_YEAR = 2022           # centre of the 2021/2023 estimate
SEXES = ("female", "male")
_QX_SOURCE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1310011401"
_IMPROVEMENT_SOURCE = ("https://www.osfi-bsif.gc.ca/en/oca/actuarial-reports/"
                       "actuarial-report-32nd-canada-pension-plan-revised-version")

QX = Rule({
    "female": (
        0.00458, 0.0003, 0.00021, 0.00016, 0.00012, 0.0001, 9e-05, 9e-05, 9e-05, 9e-05,
        0.00011, 0.00012, 0.00014, 0.00016, 0.00019, 0.00024, 0.00029, 0.00036, 0.00044,
        0.00052, 0.00061, 0.00069, 0.00075, 0.00079, 0.00082, 0.00083, 0.00084, 0.00086,
        0.00089, 0.00091, 0.00095, 0.00098, 0.00101, 0.00104, 0.00106, 0.00109, 0.00111,
        0.00114, 0.00118, 0.00122, 0.00127, 0.00133, 0.00139, 0.00146, 0.00154, 0.00163,
        0.00173, 0.00184, 0.00196, 0.00209, 0.00225, 0.00241, 0.0026, 0.0028, 0.00302,
        0.00327, 0.00354, 0.00383, 0.00416, 0.00452, 0.00491, 0.00535, 0.00584, 0.00637,
        0.00697, 0.00763, 0.00837, 0.00919, 0.0101, 0.01112, 0.01225, 0.01352, 0.01495,
        0.01654, 0.01833, 0.02033, 0.02259, 0.02513, 0.028, 0.03123, 0.03487, 0.039,
        0.04367, 0.04897, 0.05498, 0.06181, 0.06958, 0.07843, 0.08852, 0.10004, 0.11321,
        0.1279, 0.14381, 0.16094, 0.17926, 0.20047, 0.22118, 0.24291, 0.2655, 0.28876,
        0.31247, 0.3364, 0.36029, 0.38391, 0.40702, 0.42941, 0.45089, 0.47131, 0.49056,
        0.50854, 1),
    "male": (
        0.00524, 0.00037, 0.00025, 0.00018, 0.00014, 0.00012, 0.0001, 9e-05, 9e-05, 0.0001,
        0.00011, 0.00012, 0.00015, 0.00018, 0.00024, 0.00032, 0.00044, 0.00057, 0.0007,
        0.00084, 0.00099, 0.00113, 0.00127, 0.00139, 0.00149, 0.00158, 0.00166, 0.00175,
        0.00183, 0.00192, 0.002, 0.00208, 0.00215, 0.00221, 0.00226, 0.00231, 0.00235,
        0.00239, 0.00244, 0.00249, 0.00254, 0.00261, 0.00268, 0.00277, 0.00288, 0.003,
        0.00315, 0.00331, 0.0035, 0.00371, 0.00396, 0.00423, 0.00453, 0.00485, 0.0052,
        0.00559, 0.00601, 0.00648, 0.00698, 0.00754, 0.00815, 0.00882, 0.00956, 0.01038,
        0.01127, 0.01226, 0.01336, 0.01457, 0.01591, 0.01739, 0.01903, 0.02086, 0.0229,
        0.02516, 0.02768, 0.03049, 0.03363, 0.03713, 0.04106, 0.04545, 0.05038, 0.05592,
        0.06214, 0.06914, 0.07703, 0.08592, 0.09596, 0.1073, 0.12014, 0.13468, 0.15118,
        0.16935, 0.18872, 0.20921, 0.23072, 0.25395, 0.27634, 0.29927, 0.32253, 0.3459,
        0.36915, 0.39205, 0.41439, 0.43598, 0.45666, 0.4763, 0.49479, 0.51207, 0.5281,
        0.54287, 1),
}, TABLE_YEAR, _QX_SOURCE)

IMPROVEMENT = Rule({"under_90": 0.010, "90_94": 0.006, "95_plus": 0.002}, 2024, _IMPROVEMENT_SOURCE)


def _improvement(age: int) -> float:
    imp = IMPROVEMENT.value
    return imp["under_90"] if age < 90 else imp["90_94"] if age < 95 else imp["95_plus"]


def qx(sex: str | None, age: int, year: int) -> float:
    """Chance of dying during ``year`` at ``age``: the table rate improved from
    TABLE_YEAR. ``sex=None`` averages the two tables."""
    if age >= OMEGA - 1:
        return 1.0                  # "110 and over": nobody outlives the table
    if sex is None:
        base = (QX.value["female"][age] + QX.value["male"][age]) / 2
    else:
        base = QX.value[sex][age]
    return min(1.0, base * (1 - _improvement(age)) ** (year - TABLE_YEAR))


def death_cdf(person, start_year: int) -> np.ndarray:
    """``cdf[k]``: chance the person, alive at ``person.age`` on January 1 of
    ``start_year``, has died by the end of the year at age ``person.age + k``."""
    ages = range(person.age, OMEGA)
    survive = np.cumprod([1 - qx(person.sex, a, start_year + (a - person.age)) for a in ages])
    return 1 - survive


def draw_death_ages(people, start_year: int, paths: int, seed: int) -> np.ndarray:
    """(P, N) death ages, each person independent and alive through this year at
    least. Uses its own stream of ``seed``, so it never shifts the return draws."""
    rng = np.random.default_rng([seed, 1])
    out = np.empty((len(people), paths), dtype=int)
    for i, p in enumerate(people):
        cdf = death_cdf(p, start_year)
        k = np.searchsorted(cdf, rng.random(paths), side="right")
        out[i] = np.minimum(p.age + k + 1, OMEGA)
    return out


def median_death_age(person, start_year: int) -> int:
    """The death age the person reaches with a 50% chance or less (the median draw)."""
    cdf = death_cdf(person, start_year)
    return int(min(person.age + int(np.argmax(cdf >= 0.5)) + 1, OMEGA))
