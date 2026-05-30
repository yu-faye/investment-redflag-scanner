"""v10 visualization helpers: argument trees, narrative writer, Sankey
data builders.

Everything in this package is pure-Python data shaping; the actual
rendering lives in the static dashboard JS and the Streamlit app. The
modules here MUST stay free of LLM calls or stochastic output -- the
audit story relies on deterministic, reproducible reasoning chains.
"""
