"""Evaluation infrastructure.

``metrics``   behaviour precision/recall/F1, false alerts per hour, latency,
              identity accuracy / switches / duplicates / recovery /
              time-to-confirmation, FPS consistency.
``synthetic`` generated scenarios used to exercise the pipeline in CI. Every
              result produced from them is labelled ``SYNTHETIC``.

Real-world accuracy requires a labelled classroom dataset, which this
repository does not contain; such results are reported as ``NOT_EVALUATED``.
"""

SYNTHETIC = "SYNTHETIC"
NOT_EVALUATED = "NOT_EVALUATED"
EVALUATED = "EVALUATED"
