"""Tests for the results exporter that copies run evidence into results/."""

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "tools" / "export_results.py"
spec = importlib.util.spec_from_file_location("export_results", SCRIPT)
export_results = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_results)


def make_attempt(runs: Path, *, model: str, name: str, deliverable: str = "report.txt", run: str = "pilot") -> Path:
    """Create one attempt folder with a record and a single deliverable, plus the other sources the export needs."""
    attempt = runs / run / model / name
    (attempt / "workspace" / "outputs").mkdir(parents=True)
    (attempt / "record.json").write_text("{}")
    (attempt / "workspace" / "outputs" / deliverable).write_text(name)
    make_required_sources(runs)
    return attempt


SEED_FILES = {
    "ocr/seed/seed0000/record.json": "runs/ocr/seed/seed0000/record.json",
    "pilot/review/verdicts_backup/verdicts/R00-00.json": "review/verdicts/R00-00.json",
    "pilot/review/verification/check.json": "review/verification/check.json",
}


def make_required_sources(runs: Path) -> None:
    """Give every source the exporter needs one file, so a test only adds the files it is about."""
    for source in SEED_FILES:
        if not (runs / source).exists():
            write(runs / source, "{}")


def seeded_outputs(target: Path) -> list[Path]:
    """Where the export puts the files make_required_sources created."""
    return [target / destination for destination in SEED_FILES.values()]


def write(path: Path, text: str) -> None:
    """Write a small file, creating its parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestResultsExporter:
    """ResultsExporter copies only final attempts, real files, and refuses folder-name collisions."""

    def test_underscore_folders_and_failed_attempts_are_skipped(self, tmp_path: Path) -> None:
        """_grading and .failed- attempts stay out of results; the final attempt is exported."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="0123456789abcdef")
        make_attempt(runs, model="claude", name="0123456789abcdef.failed-1")
        make_attempt(runs, model="_grading", name="opaque0001")
        export_results.ResultsExporter(runs=runs, target=target).export()
        exported = target / "runs" / "pilot"
        assert sorted(p.name for p in exported.iterdir()) == ["claude"]
        assert [p.name for p in (exported / "claude").iterdir()] == ["01234567"]
        assert (exported / "claude" / "01234567" / "deliverables" / "report.txt").read_text() == "0123456789abcdef"

    def test_symlinked_deliverables_are_not_exported(self, tmp_path: Path) -> None:
        """A symlink in the outputs folder, which could point anywhere on the host, is left behind."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        secret = tmp_path / "secret.txt"
        secret.write_text("host file")
        attempt = make_attempt(runs, model="claude", name="0123456789abcdef")
        (attempt / "workspace" / "outputs" / "link.txt").symlink_to(secret)
        export_results.ResultsExporter(runs=runs, target=target).export()
        deliverables = target / "runs" / "pilot" / "claude" / "01234567" / "deliverables"
        assert sorted(p.name for p in deliverables.iterdir()) == ["report.txt"]

    @pytest.mark.parametrize("swapped", ["outputs", "workspace"])
    def test_a_symlinked_outputs_or_workspace_folder_exports_nothing(self, tmp_path: Path, swapped: str) -> None:
        """An agent that swaps its outputs or workspace folder for a link cannot steer the export onto host files."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        host = tmp_path / "host"
        (host / "outputs").mkdir(parents=True)
        (host / "secret.txt").write_text("host file")
        (host / "outputs" / "secret.txt").write_text("host file")
        attempt = make_attempt(runs, model="claude", name="0123456789abcdef")
        folder = attempt / "workspace" / "outputs" if swapped == "outputs" else attempt / "workspace"
        shutil.rmtree(folder)
        folder.symlink_to(host if swapped == "workspace" else host / "outputs", target_is_directory=True)
        export_results.ResultsExporter(runs=runs, target=target).export()
        assert not [path for path in target.rglob("secret.txt")]

    def test_attempts_that_shorten_to_the_same_name_raise(self, tmp_path: Path) -> None:
        """Two attempts sharing their first eight characters stop the export instead of overwriting."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="01234567aaaa")
        make_attempt(runs, model="claude", name="01234567bbbb")
        with pytest.raises(export_results.ExportCollisionError, match="01234567aaaa and 01234567bbbb"):
            export_results.ResultsExporter(runs=runs, target=target).export()

    def test_a_collision_leaves_earlier_results_untouched(self, tmp_path: Path) -> None:
        """The collision is found before the old results/runs folder is cleared."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        earlier = target / "runs" / "pilot" / "keep.md"
        earlier.parent.mkdir(parents=True)
        earlier.write_text("previous export")
        make_attempt(runs, model="claude", name="01234567aaaa")
        make_attempt(runs, model="claude", name="01234567bbbb")
        with pytest.raises(export_results.ExportCollisionError):
            export_results.ResultsExporter(runs=runs, target=target).export()
        assert earlier.read_text() == "previous export"

    def test_same_short_name_under_different_models_is_fine(self, tmp_path: Path) -> None:
        """The eight-character name only has to be unique within one model's folder."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="01234567aaaa")
        make_attempt(runs, model="gemini", name="01234567bbbb")
        export_results.ResultsExporter(runs=runs, target=target).export()
        assert (target / "runs" / "pilot" / "gemini" / "01234567" / "record.json").is_file()


COMMITTED = ("runs/ocr/keep.json", "review/verdicts/A01-01.json")


def commit_evidence(target: Path) -> list[Path]:
    """Files standing in for evidence already committed under results/."""
    paths = [target / name for name in COMMITTED]
    for path in paths:
        write(path, "committed evidence")
    return paths


class TestExportSources:
    """The exporter rebuilds results/ only when every source it copies from has something in it."""

    @pytest.mark.parametrize(
        ("source", "removed"),
        [
            ("pilot", "pilot"),
            ("ocr", "ocr"),
            ("pilot/review/verdicts_backup/verdicts", "pilot/review/verdicts_backup/verdicts"),
            ("pilot/review/verification", "pilot/review/verification"),
            ("pilot", "pilot/claude"),
            ("ocr", "ocr/seed"),
            ("pilot/review/verdicts_backup/verdicts", "pilot/review/verdicts_backup/verdicts/R00-00.json"),
            ("pilot/review/verification", "pilot/review/verification/check.json"),
        ],
        ids=["no-pilot", "no-ocr", "no-verdicts", "no-verification"]
        + ["empty-pilot", "empty-ocr", "empty-verdicts", "empty-verification"],
    )
    def test_a_missing_or_empty_source_stops_the_export_before_anything_is_deleted(
        self, tmp_path: Path, source: str, removed: str
    ) -> None:
        """A fresh clone, or a partial restore that left folders empty, must not wipe the committed evidence."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="0123456789abcdef")
        gone = runs / removed
        shutil.rmtree(gone) if gone.is_dir() else gone.unlink()
        kept = commit_evidence(target)
        with pytest.raises(export_results.MissingSourceError, match=re.escape(source)):
            export_results.ResultsExporter(runs=runs, target=target).export()
        assert [path.read_text() for path in kept] == ["committed evidence"] * 2

    def test_every_missing_source_is_named_and_nothing_changes(self, tmp_path: Path) -> None:
        """One run of the script tells the operator everything that is missing, and that results/ is untouched."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="0123456789abcdef")
        shutil.rmtree(runs / "ocr")
        shutil.rmtree(runs / "pilot" / "review" / "verification")
        with pytest.raises(export_results.MissingSourceError) as raised:
            export_results.ResultsExporter(runs=runs, target=target).export()
        assert "ocr" in str(raised.value)
        assert "pilot/review/verification" in str(raised.value)
        assert "nothing was changed" in str(raised.value)

    def test_every_evidence_file_lands_at_its_exact_path(self, tmp_path: Path) -> None:
        """Run reports of both runs, grades, nested deliverables, verdicts and verification files are all counted."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        attempt = make_attempt(runs, model="gemini", name="0123456789abcdef")
        rerun = make_attempt(runs, model="claude", name="fedcba9876543210", run="ocr")
        for run in ("pilot", "ocr"):
            for name in export_results.RUN_FILES:
                write(runs / run / name, name)
        write(attempt / "workspace" / "outputs" / "sub" / "dir" / "a.txt", "nested")
        (attempt / "workspace" / "outputs" / "emptydir").mkdir()
        write(attempt / "grade.txt", "not a grade file")
        write(runs / "pilot" / "review" / "verdicts_backup" / "verdicts" / "notes.txt", "not json")
        write(attempt / "transcript.jsonl", "{}")
        write(attempt / "grade.opus.json", "{}")
        write(attempt / "grade.opus.gold.json", "{}")
        write(runs / "pilot" / "review" / "verdicts_backup" / "verdicts" / "A01-01.json", "{}")
        write(runs / "pilot" / "review" / "verification" / "result1.json", "[]")
        copied = export_results.ResultsExporter(runs=runs, target=target).export()
        attempt_out = target / "runs" / "pilot" / "gemini" / "01234567"
        rerun_out = target / "runs" / "ocr" / "claude" / rerun.name[:8]
        run_files = [target / "runs" / run / name for run in ("pilot", "ocr") for name in export_results.RUN_FILES]
        expected = (
            run_files
            + seeded_outputs(target)
            + [
                attempt_out / "record.json",
                attempt_out / "transcript.jsonl",
                attempt_out / "grade.opus.json",
                attempt_out / "grade.opus.gold.json",
                attempt_out / "deliverables" / "report.txt",
                attempt_out / "deliverables" / "sub" / "dir" / "a.txt",
                rerun_out / "record.json",
                rerun_out / "deliverables" / "report.txt",
                target / "review" / "verdicts" / "A01-01.json",
                target / "review" / "verification" / "result1.json",
            ]
        )
        assert [path for path in expected if not path.is_file()] == []
        assert copied == len(expected)
        assert sorted(path for path in target.rglob("*") if path.is_file()) == sorted(expected)

    def test_a_re_export_drops_files_that_are_no_longer_in_runs(self, tmp_path: Path) -> None:
        """results/ mirrors runs/, so a stale attempt or verdict from an earlier export does not linger."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="0123456789abcdef")
        stale = [
            target / "runs" / "pilot" / "claude" / "deadbeef" / "record.json",
            target / "runs" / "ocr" / "gone0000" / "record.json",
            target / "review" / "verdicts" / "old.json",
        ]
        for path in stale:
            write(path, "stale")
        export_results.ResultsExporter(runs=runs, target=target).export()
        assert [path for path in stale if path.exists()] == []


class TestCommandLine:
    """The script reports problems as a message and an exit code, not a traceback."""

    def test_a_missing_source_exits_with_the_message(self, tmp_path: Path) -> None:
        """The operator sees what is missing and that results/ was left alone."""
        target = tmp_path / "results"
        with pytest.raises(SystemExit) as raised:
            export_results.main(["--runs", str(tmp_path / "runs"), "--target", str(target)])
        assert "nothing was changed" in str(raised.value.code)
        assert not target.exists()

    def test_a_complete_export_prints_the_count(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A successful run says how many files it copied and where."""
        runs, target = tmp_path / "runs", tmp_path / "results"
        make_attempt(runs, model="claude", name="0123456789abcdef")
        export_results.main(["--runs", str(runs), "--target", str(target)])
        assert capsys.readouterr().out == f"copied 5 files into {target}\n"
