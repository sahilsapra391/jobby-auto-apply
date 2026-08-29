# apps/web — the board (Phase 2)

Next.js on Vercel, git-deployed. Empty until Phase 2 rewrites the board's data
layer onto keyset pagination and the tenant channel.

## The state of the decision, and one thing §2 gets wrong about it

§2 chooses "Next.js on Vercel, git-deployed" and justifies it with "already
works, already deploys on merge. Do not move it during a rebuild that is
changing everything else."

Half of that is accurate and half of it is not, and the difference matters to
whoever picks Phase 2 up. **Vercel and git-deploy are already true.** The
deployed board is a Vercel project rooted at `deploy/`, built from
`main` on merge. **Next.js is not.** The board today is
`deploy/api/page.py`, a Python serverless function that renders sanitised KV
state with a bundled copy of the renderer, plus `deploy/api/board.js` for the
queue, document, logo, health and provider endpoints. There is no Next.js
anywhere in the repository.

So adopting Next.js is a rewrite of the web surface, not a continuation of it,
and the sentence "do not move it" cannot mean both "keep what is there" and
"build it in Next.js". Read the decision as: *the host and the deploy trigger
stay; the framework is new in Phase 2.* That is the only reading consistent
with Phase 2 retiring `jobby:board-state` and the publish floor.

The migration is also not free in a way §2 does not mention: `deploy/api/page.py`
is what `scripts/sync_vercel_renderer.py` keeps in step with `scripts/`, and
that sync is enforced by CI. Whatever replaces it has to replace that check too,
or the outage it was written for comes back.
