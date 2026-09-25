"""Pipeline errors carry the process exit code defined in PRD Section 7."""

EXIT_OK = 0
EXIT_SOURCE_UNAVAILABLE = 2
EXIT_VALIDATION_FAILED = 3
EXIT_INTERNAL = 4


class PipelineError(Exception):
    exit_code = EXIT_INTERNAL


class SourceUnavailable(PipelineError):
    """A source could not be fetched (not published, network down, bad response)."""

    exit_code = EXIT_SOURCE_UNAVAILABLE


class CompletenessError(PipelineError):
    """A source was fetched but fails its completeness check."""

    exit_code = EXIT_SOURCE_UNAVAILABLE


class StageCheckFailed(PipelineError):
    """An inter-stage invariant did not hold (e.g. raw.trips count != manifest)."""

    exit_code = EXIT_INTERNAL
