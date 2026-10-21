from pathlib import Path


class Workspace:
    """The only part of the filesystem the file tools may touch."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        path = (self.root / (relative or ".")).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path is outside the workspace folder")
        return path
