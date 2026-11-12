"""Copy the evidence a reviewer needs from the git-ignored runs/ folder into results/."""

import argparse
import shutil
from pathlib import Path

RUNS = ("pilot", "ocr")
ATTEMPT_FILES = ("record.json", "transcript.jsonl")
RUN_FILES = ("report.md", "report.csv", "review_sample.json", "ledger.jsonl")
SHORT_NAME = 8
VERDICTS = Path("pilot") / "review" / "verdicts_backup" / "verdicts"
VERIFICATION = Path("pilot") / "review" / "verification"


class ExportCollisionError(Exception):
    """Two attempts would be exported to the same shortened folder name."""


class MissingSourceError(Exception):
    """A source the export copies from is missing or empty."""


class ResultsExporter:
    """Builds results/ from runs/ without the bulky judge workspaces or cached inputs."""

    def __init__(self, *, runs: Path, target: Path) -> None:
        """Store the source runs folder and the target results folder."""
        self.__runs = runs
        self.__target = target

    @staticmethod
    def __deliverables(*, attempt: Path, outputs: Path) -> list[Path]:
        """Regular files under outputs that really live there.

        The agent controls its workspace, so an outputs (or workspace) folder replaced by a symlink exports nothing,
        and a file is only exported when its resolved path stays under the resolved outputs folder.
        """
        if outputs.is_symlink() or not outputs.is_dir():
            return []
        root = outputs.resolve()
        if not root.is_relative_to(attempt.resolve()):
            return []
        return [
            item
            for item in sorted(outputs.rglob("*"))
            if item.is_file() and not item.is_symlink() and item.resolve().is_relative_to(root)
        ]

    def export(self) -> int:
        """Rebuild the target folder and return the number of files copied.

        A missing or empty source, or a name collision, stops it before anything in the target is deleted.
        """
        plans = {run: self.__attempts(run=run) for run in RUNS}
        self.__check_sources(plans=plans)
        shutil.rmtree(self.__target / "runs", ignore_errors=True)
        copied = 0
        for run in RUNS:
            copied += self.__export_run(run=run, attempts=plans[run])
        copied += self.__export_review()
        return copied

    def __check_sources(self, *, plans: dict[str, list[tuple[Path, Path]]]) -> None:
        """Refuse to start unless every run has a recorded attempt and every review folder has its JSON files."""
        missing = [run for run in RUNS if not any((attempt / "record.json").is_file() for attempt, _ in plans[run])]
        for folder in (VERDICTS, VERIFICATION):
            if not any((self.__runs / folder).glob("*.json")):
                missing.append(str(folder))
        if missing:
            raise MissingSourceError(
                f"{self.__runs} has nothing to export in {', '.join(missing)}; nothing was changed in {self.__target}. "
                "The export rebuilds results/ from runs/, so every run needs an attempt with a record.json "
                "and each review folder needs its JSON files."
            )

    def __attempts(self, *, run: str) -> list[tuple[Path, Path]]:
        """Pair every final attempt of a run with its shortened export folder, refusing two that share a folder."""
        source = self.__runs / run
        claimed: dict[Path, Path] = {}
        for attempt in sorted(source.glob("*/*")) if source.is_dir() else []:
            if not attempt.is_dir() or attempt.parent.name.startswith("_") or ".failed-" in attempt.name:
                continue
            destination = self.__target / "runs" / run / attempt.parent.name / attempt.name[:SHORT_NAME]
            if destination in claimed:
                raise ExportCollisionError(
                    f"{claimed[destination].name} and {attempt.name} in {attempt.parent} both export as "
                    f"{destination.name}; one would overwrite the other"
                )
            claimed[destination] = attempt
        return [(attempt, destination) for destination, attempt in claimed.items()]

    def __export_run(self, *, run: str, attempts: list[tuple[Path, Path]]) -> int:
        """Copy run-level reports and every final attempt's record, transcript, grades and deliverables."""
        source = self.__runs / run
        copied = 0
        for name in RUN_FILES:
            if (source / name).is_file():
                copied += self.__copy(source=source / name, destination=self.__target / "runs" / run / name)
        for attempt, destination in attempts:
            for name in ATTEMPT_FILES:
                if (attempt / name).is_file():
                    copied += self.__copy(source=attempt / name, destination=destination / name)
            for grade in attempt.glob("grade.*.json"):
                copied += self.__copy(source=grade, destination=destination / grade.name)
            outputs = attempt / "workspace" / "outputs"
            for item in self.__deliverables(attempt=attempt, outputs=outputs):
                relative = item.relative_to(outputs)
                copied += self.__copy(source=item, destination=destination / "deliverables" / relative)
        return copied

    def __export_review(self) -> int:
        """Copy the human review verdicts and the second-pass verification files."""
        copied = 0
        for folder, name in ((self.__runs / VERDICTS, "verdicts"), (self.__runs / VERIFICATION, "verification")):
            target = self.__target / "review" / name
            shutil.rmtree(target, ignore_errors=True)
            for item in sorted(folder.glob("*.json")):
                copied += self.__copy(source=item, destination=target / item.name)
        return copied

    @staticmethod
    def __copy(*, source: Path, destination: Path) -> int:
        """Copy one file, creating parent folders, and count it."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return 1


def main(argv: list[str] | None = None) -> None:
    """Export runs/ into results/, turning a refused export into an exit message instead of a traceback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    parser.add_argument("--target", type=Path, default=Path("results"))
    arguments = parser.parse_args(argv)
    exporter = ResultsExporter(runs=arguments.runs, target=arguments.target)
    try:
        copied = exporter.export()
    except (MissingSourceError, ExportCollisionError) as error:
        raise SystemExit(str(error)) from error
    print(f"copied {copied} files into {arguments.target}")


if __name__ == "__main__":
    main()
