"""FactCircuit double-loop model runner.

Thin canonical entry point over ``newsverify.double_loop`` so that
``python -m factcircuit.double_loop`` works alongside the ``factcircuit``
command. The implementation and schemas are unchanged.
"""

from newsverify.double_loop import *  # noqa: F401,F403
from newsverify.double_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
