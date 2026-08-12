from pathlib import Path
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


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skill(name: str) -> str:
    """Return a skill's SKILL.md contents for `Agent(skills=[...])`.

    Passing the skill's *directory* silently loads nothing: `discover_skills()`
    scans a path's children for sub-directories containing SKILL.md, so a
    directory that holds SKILL.md itself yields an empty list and the agent runs
    with no skill at all — no error, just an agent that ignores its playbook.
    Passing the file contents registers it as an inline skill, fully loaded.
    """
    return (SKILLS_DIR / name / "SKILL.md").read_text()
