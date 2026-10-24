"""A project: the directory the generator reads and writes (docs/spec.md §3.6.9).

The interface declaration is `<project>/codegen.toml` or, when there is none,
`<project>/model/codegen.toml`. Its `[project]` table says where the model is
(default `model`), where the outputs go (default `generated`), where the
catalogue is (default `catalogue`), where the property items are (optional),
and the names of the generated Rust crate and Python package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, read_config
from .errors import Diagnostic, GeneratorError


@dataclass
class Project:
    root: Path
    toml: str  # the declaration, relative to root
    cfg: Config
    problems: list = field(default_factory=list)  # (construct, section, message) of read_config

    @property
    def model(self) -> str:
        return self.cfg.project["model"]

    @property
    def generated(self) -> str:
        return self.cfg.project["generated"]

    def m(self, name: str) -> str:
        """A file of the model directory, relative to root."""
        return "%s/%s" % (self.model, name)

    def path(self, rel: str) -> Path:
        return self.root / rel

    @property
    def gen_dir(self) -> Path:
        return self.root / self.generated

    @property
    def catalogue(self) -> Path:
        return self.root / self.cfg.project["catalogue"]

    @property
    def properties(self) -> Path | None:
        p = self.cfg.project.get("properties")
        return self.root / p if p else None

    def shown(self, file: str) -> str:
        """A file name as the front end writes it (model/..., generated/...), as it is in this project."""
        for default, actual in (("model/codegen.toml", self.toml), ("model/", self.model + "/"),
                                ("generated/", self.generated + "/")):
            if file.startswith(default):
                return actual + file[len(default):]
        return file

    def relocate(self, e: GeneratorError) -> GeneratorError:
        """The error with its file names as they are in this project."""
        return GeneratorError([Diagnostic(d.code, self.shown(d.file), d.where, d.section, d.message) for d in e.diagnostics])


def load(root) -> Project:
    """The project at root; G6 when the declaration is missing, unreadable or
    its [project] table says something impossible."""
    root = Path(root)
    top, inner = root / "codegen.toml", root / "model" / "codegen.toml"
    if top.is_file() and inner.is_file():
        raise GeneratorError([Diagnostic("G6", "codegen.toml", "file", "§3.6.9",
                                         "both codegen.toml and model/codegen.toml exist: keep one")])
    if not top.is_file() and not inner.is_file():
        raise GeneratorError([Diagnostic("G6", "codegen.toml", "file", "§3.1.1, §3.6.9",
                                         "missing: neither %s/codegen.toml nor %s/model/codegen.toml" % (root, root))])
    rel = "codegen.toml" if top.is_file() else "model/codegen.toml"
    cfg, problems = read_config((root / rel).read_text(encoding="utf-8"))
    if rel != "codegen.toml" and cfg.project["model"] != "model":
        problems.append(("project.model", "§3.6.9",
                         "model/codegen.toml is in the model directory model; move it to the project root to name another"))
    return Project(root, rel, cfg, problems)
