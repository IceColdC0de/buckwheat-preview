"""Uninstall result wording shared by the CLI and the TUI.

This module imports nothing, so the CLI can use it without loading the
adapters (``--version`` must stay fast). Both front ends read these strings,
so they cannot drift apart.
"""

LEFT_BEHIND_HEADER = (
    "Still in your files. Buckwheat never edits lines outside its own block, "
    "so remove these by hand:"
)


def partly_removed(snapshot_id: str) -> str:
    """The result line when uninstall left something in the user's files."""
    return (
        "Removed what buckwheat could remove. "
        f"A backup is kept as snapshot {snapshot_id}."
    )
