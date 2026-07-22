"""App-declared structured error codes for github-connector.

These pair with the platform taxonomy (`imperal_sdk.chat.error_codes`) for
cases that taxonomy doesn't cover — problems specific to reaching a *user's*
GitHub account, not the Imperal backend itself. Every code here matches
the SDK's app-declared pattern `^[A-Z][A-Z0-9_]{2,63}$`
(imperal_sdk.types.action_result.ActionResult.error).

Platform codes (imported directly where they apply — permission, rate limit,
backend 5xx, validation, internal) are used as-is; these GH_* codes only
exist where no platform code honestly fits.
"""

GH_NOT_CONNECTED = "GH_NOT_CONNECTED"                 # user has no GitHub account connected on file
GH_REPO_NOT_ACCESSIBLE = "GH_REPO_NOT_ACCESSIBLE"     # repo doesn't exist, or the connected account can't reach it
GH_API_ERROR = "GH_API_ERROR"                         # GitHub REST/GraphQL call failed (network/4xx/5xx not covered above)
GH_RATE_LIMITED = "GH_RATE_LIMITED"                   # GitHub's own rate limit (distinct from platform RATE_LIMITED)
GH_FILE_NOT_FOUND = "GH_FILE_NOT_FOUND"               # path doesn't exist at that ref
GH_CONFIRM_REQUIRED = "GH_CONFIRM_REQUIRED"           # destructive/write call made without confirm=true (preview only)
GH_WEBHOOK_SIGNATURE_INVALID = "GH_WEBHOOK_SIGNATURE_INVALID"  # webhook HMAC check failed
GH_INVALID_REVIEW_EVENT = "GH_INVALID_REVIEW_EVENT"   # review event isn't one of APPROVE/REQUEST_CHANGES/COMMENT
GH_WEBHOOK_REGISTRATION_FAILED = "GH_WEBHOOK_REGISTRATION_FAILED"  # could not create/delete a per-repo webhook (§12.2 fan-out)
