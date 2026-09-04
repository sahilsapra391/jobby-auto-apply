"""Silence every dead-man ping reason but one, derived from `watch.PING_REASONS`.

Not a test module: the name does not match `test_*.py`, so no runner collects
it. It is the shared harness for the four modules that assert on
`watch.unhealthy_reason`, the way `pgtest` is the harness for a database and
`store_isolation` is the harness for the file store.

**It exists because four modules were each restating the same five warning
names as strings.** That is the shape every list in this repository has drifted
through -- `GATE_PATHS` naming the caller and not the writer,
`verify_parity.FIELDS` comparing 11 of 28 columns, the migrations table list,
the generated-file list. Here the drift would be silent in the worst available
way: add a sixth reason, forget one of the four modules, and what you have is a
warning that is composed and never joined. Correct, exercised, never once run.

Deriving from `PING_REASONS` means a new reason is covered by all four the
moment it is added to the tuple, and `only()` raises rather than skipping when
a name is not there, so a rename fails loudly instead of quietly testing four
reasons out of six.
"""

HEARD = "HEARD"


def names(watch):
    return [reason.__name__ for reason in watch.PING_REASONS]


def only(watch, name, message=HEARD):
    """A `PING_REASONS` tuple in which just `name` has something to say.

    Patch it over `watch.PING_REASONS`. Raises `LookupError` for a name that is
    not in the tuple, because silently testing nothing is the failure this
    module exists to prevent.
    """
    found = [i for i, reason in enumerate(watch.PING_REASONS)
             if reason.__name__ == name]
    if not found:
        raise LookupError(
            f"{name!r} is not one of watch.PING_REASONS ({names(watch)}). "
            f"If it was renamed, this test is silently checking the others.")
    keep = found[0]
    return tuple((lambda m=message: m) if i == keep else (lambda: "")
                 for i in range(len(watch.PING_REASONS)))


def each(watch, message=HEARD):
    """(name, patched tuple) for every reason, so a test can cover them all."""
    return [(reason.__name__, only(watch, reason.__name__, message))
            for reason in watch.PING_REASONS]


def several(watch, messages):
    """A `PING_REASONS` tuple where each named reason returns its message.

    For the claim `only()` cannot express: that the reasons are *joined*
    rather than the first one winning. A text scan of the composition could
    never see that difference either.
    """
    for name in messages:
        if name not in names(watch):
            raise LookupError(f"{name!r} is not one of {names(watch)}")
    return tuple((lambda m=messages[reason.__name__]: m)
                 if reason.__name__ in messages else (lambda: "")
                 for reason in watch.PING_REASONS)
