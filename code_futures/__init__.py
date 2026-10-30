"""Code Futures: given a repository, say what state its engineering practice is in.

Named after Kent Beck's framing in his talk *Features Versus Futures* (2026):
features are what software does now, futures are what remains possible later,
and every feature spends some of the latter. The argument underneath it --
that structure creates options rather than value today -- is developed in
*Tidy First?*.

Not one probe here is a feature. A test suite, a branch gate, a linter, a
licence do nothing for a user today, and every one of them decides what can
still be changed later.
"""

__version__ = "1.0.0"
