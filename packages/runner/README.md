# packages/runner: the device process (Phase 4)

Empty until Phase 4, which collapses `ai.jobby.watch`, `ai.jobby.documents` and
`ai.jobby.providers` into one supervised async process with task groups, under a
single launchd plist with `KeepAlive`.

Three services exist today for one reason: a slow provider probe delayed a
Connect click, so the control lane was split out. §6 is explicit that the fix is
an async task inside one process, not a second daemon, but the constraint that
created the split is real and the replacement has to honour it.

## What must not be lost in the collapse

Everything in §8's "must survive the rebuild, unchanged" table lives on this
side of the line, and none of it is refactoring surface:

- the browser firewall in `scripts/browser_mcp_proxy.py`, which removes
  navigation, JavaScript, shell, storage and routing tools, and binds uploads by
  path, inode, size and SHA-256;
- the document integrity gates, which caught 16 real "Dear Dear" letters that
  employers had already received;
- the submission nonce state machine;
- provider keys at 0600, which never leave the machine.

The new surfaces are the ones to write the negative tests for first: a lease
signed with the wrong key, a lease for another account, an update arriving
mid-submit, and a `SIGTERM` that must leave the staging directory empty **on
disk**. The filesystem is the evidence, not a mocked handler.
