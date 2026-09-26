"""EHR evidence application.

Evidence-grounded question answering over OMOP patient records and
explanation of inherited GNN diagnosis predictions (research prototype).

Importing this package must stay side-effect free: it must not import the
upstream GNN code, torch, torch_geometric or any model runtime, and must not
contact any service. Heavy dependencies are imported inside the modules that
need them.
"""

__version__ = "0.7.0"
