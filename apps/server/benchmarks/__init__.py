"""Alignment benchmark harness (hizalama-v2 P1).

Run manually on intel — never in CI, never in the image. `metrics` is pure and
unit-tested; `datasets` fetches/loads ground truth; `run` drives the pipeline
modules over the config matrix and writes committed JSON reports to results/.
"""
