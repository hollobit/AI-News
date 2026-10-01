"""A path-backed mapping so publishing never retains the whole site in RAM."""
from collections.abc import Mapping
from pathlib import Path


class FileAssets(Mapping):
    def __init__(self, root, names):
        self.root=Path(root);self.names=tuple(names);self.allowed=frozenset(names)
    def __len__(self): return len(self.names)
    def __iter__(self): return iter(self.names)
    def __contains__(self, name): return name in self.allowed
    def __getitem__(self, name):
        if name not in self.allowed: raise KeyError(name)
        return (self.root/name).read_text()
