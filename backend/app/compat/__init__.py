"""Hardware compatibility checker — shared utilities used by P1 (tree CRUD).

P2 adds the findings engine; P3 adds AI scan.  Only normalize and units are
needed here: parameter writes at write-time validate dimension + resolve the
dictionary entry.
"""
