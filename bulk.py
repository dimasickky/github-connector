"""github-connector · the shared shape every bulk operation here uses.

Written once, on purpose. `extensions/batching.md` §7 puts it plainly: the
preview gate and the size ceiling have to be a common pattern, "иначе
следующий bulk-хендлер снова получится «на свой вкус»". Every batch tool in
this extension goes through `run_bulk` below, so they cannot drift apart.

Three things this enforces, and why each one earns its place:

1. **Preview before a destructive batch.** Our single-object destructive
   tools (`delete_branch`, `close_pull_request_or_issue`) already refuse to
   act without `confirm=true`. A batch touches *more*, so the gate must be at
   least as strong — §7 point 2 notes ours was in fact weaker, because no
   batch existed to carry one. The preview lists every target by name: the
   realistic accident is not "I didn't want to delete branches", it is "I
   didn't realise `feature/auth` was in that list".

2. **A ceiling on batch size.** Not bureaucracy — GitHub applies secondary
   rate limits to mutating requests, and an unbounded fan-out is a
   self-inflicted 403 halfway through a *destructive* run. A refusal up front
   is honest and recoverable; a partial failure at item 60 of 200 is neither.

3. **Bounded concurrency with an honest partial report.** Same reasoning, and
   the same constant, as `tasks._BULK_CONCURRENCY` (see `tasks` v3.38.1): a
   serial loop over 40 items walks into the 180s a normal tool call gets, and
   timing out mid-batch is the worst outcome for a destructive operation
   because some items are already gone while the caller is told it failed.
   Eight in flight turns ~40 round trips into ~5 rounds and stays polite to
   the backend.

Per-item failure is never fatal to the batch: each item reports its own
outcome, and the summary states successes and failures separately. A caller
who asked for eight deletions is entitled to know exactly which six happened.
"""
import asyncio

from imperal_sdk import ActionResult, sdl
from imperal_sdk.chat.error_codes import VALIDATION_MISSING_FIELD

# How many mutating GitHub requests a batch may have in flight at once.
# Matches tasks._BULK_CONCURRENCY — same trade-off, same number, deliberately
# not re-tuned per extension.
BULK_CONCURRENCY = 8

# Largest batch we accept in one call. GitHub's secondary rate limits punish
# bursts of mutating requests, and a destructive run that dies partway is
# unrecoverable, so we refuse oversized batches up front instead of failing
# in the middle of one.
MAX_BULK_ITEMS = 50


class BulkItemResult(sdl.Entity):
    """One item's outcome inside a batch — success or the reason it failed."""
    ok: bool = False
    error: str = ""


class BulkResult(sdl.EntityList[BulkItemResult]):
    """A batch's outcome: per-item results plus the two counts that matter.

    `needs_confirmation` doubles as the preview flag — on a preview pass the
    items carry the intended targets and nothing has been touched yet.
    """
    action: str = ""
    succeeded: int = 0
    failed: int = 0
    needs_confirmation: bool = False


def _plural(n: int, word: str) -> str:
    """'1 branch' / '3 branches' — the -es case matters because our two kinds
    are 'branch' and 'issue_or_pr', and '3 branchs' in a destructive summary
    reads like a bug in the tool the user is about to trust with deletions."""
    if n == 1:
        return f"{n} {word}"
    suffix = "es" if word.endswith(("ch", "sh", "s", "x", "z")) else "s"
    return f"{n} {word}{suffix}"


def validate_batch(items: list, what: str) -> ActionResult | None:
    """Reject an empty or oversized batch. Returns None when the batch is fine.

    Checked before any network call: refusing early is the whole point of the
    ceiling, since the failure mode it prevents is a half-finished run.
    """
    if not items:
        return ActionResult.error(
            f"No {what} given — pass at least one.",
            code=VALIDATION_MISSING_FIELD,
        )
    if len(items) > MAX_BULK_ITEMS:
        return ActionResult.error(
            f"{len(items)} {what} is over the {MAX_BULK_ITEMS}-item limit for one batch. "
            f"GitHub rate-limits bursts of changes, and a batch that dies halfway "
            f"through is worse than one that never started — split it up.",
            code=VALIDATION_MISSING_FIELD,
        )
    return None


def preview(action: str, targets: list[str], kind: str, detail: str) -> ActionResult:
    """The confirm gate: list exactly what would be touched, change nothing.

    `detail` spells out the consequence in the caller's own words (a deleted
    branch is not a closed issue) — the gate is only useful if it says what
    is actually about to happen.
    """
    items = [
        BulkItemResult(id=t, title=t, kind=kind, ok=False, error="")
        for t in targets
    ]
    return ActionResult.success(
        BulkResult(
            items=items, total=len(items), action=action,
            succeeded=0, failed=0, needs_confirmation=True,
        ),
        summary=(
            f"This will {action} {_plural(len(targets), kind)}: "
            f"{', '.join(targets)}. {detail} "
            f"Call again with confirm=true to go ahead."
        ),
    )


async def run_bulk(action: str, targets: list[str], kind: str, do_one) -> ActionResult:
    """Run `do_one(target)` across the batch, bounded, and report honestly.

    `do_one` returns None on success or a human-readable reason on failure; it
    must not raise for an expected API rejection. Anything unexpected that
    does escape is caught here and attributed to its own item rather than
    taking down results already earned by the rest of the batch.

    Results keep the caller's ordering: a report you have to re-sort against
    what you asked for is a report you cannot trust at a glance.
    """
    sem = asyncio.Semaphore(BULK_CONCURRENCY)

    async def _one(target: str) -> BulkItemResult:
        async with sem:
            try:
                err = await do_one(target)
            except Exception as e:  # noqa: BLE001 — one item's crash is not the batch's
                err = f"unexpected error: {type(e).__name__}"
            return BulkItemResult(
                id=target, title=target, kind=kind,
                ok=err is None, error=err or "",
            )

    items = await asyncio.gather(*(_one(t) for t in targets))
    succeeded = sum(1 for i in items if i.ok)
    failed = len(items) - succeeded

    if failed == 0:
        summary = f"{action.capitalize()}d {_plural(succeeded, kind)}."
    elif succeeded == 0:
        summary = f"Could not {action} any of the {_plural(len(items), kind)}."
    else:
        broken = ", ".join(f"{i.title} ({i.error})" for i in items if not i.ok)
        summary = (
            f"{action.capitalize()}d {succeeded} of {_plural(len(items), kind)} — "
            f"{failed} failed: {broken}"
        )

    return ActionResult.success(
        BulkResult(
            items=list(items), total=len(items), action=action,
            succeeded=succeeded, failed=failed, needs_confirmation=False,
        ),
        summary=summary,
    )
