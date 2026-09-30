"""Canadian retirement planner.

See docs/superpowers/specs/2026-09-29-retirement-planner-design.md. Library use::

    from stockanalysis.retirement import load_inputs, run
    result = run(load_inputs("retirement/plan.json"))
    result.simulated.success
"""
from .engine import PlanResult, Projection, run
from .inputs import PlanInputs, load_inputs

__all__ = ["PlanInputs", "PlanResult", "Projection", "load_inputs", "run"]
