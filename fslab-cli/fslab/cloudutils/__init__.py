"""Cloud-provider-specific utility helpers.

This package is the home for thin SDK wrappers (boto3, google-cloud,
azure-sdk, ...) and credential / lifecycle helpers that are shared
across pipelines (build, publish, run). Pipeline-agnostic; nothing
inside should import from fslab.bitstream or fslab.pipeline.
"""
