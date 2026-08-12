import os


def crew_verbose() -> bool:
    """Whether crews print their execution panels.

    On by default — it's how you see an agent working during a demo. Turn it off
    (`CREW_VERBOSE=false`) for harnesses and batch runs, where the panels bury
    the result.
    """
    return os.getenv("CREW_VERBOSE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
