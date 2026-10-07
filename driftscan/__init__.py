"""driftscan - find out whether two stores actually agree, and say so honestly."""

from .compare import Bucket, Divergence, Report, aggregate, bucket_of, compare, row_hash
from .normalize import RULES, Rule, normalize
from .report import render

__all__ = ["compare", "aggregate", "bucket_of", "row_hash", "Report", "Bucket",
           "Divergence", "normalize", "RULES", "Rule", "render"]
__version__ = "0.1.0"
