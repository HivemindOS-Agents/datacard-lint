"""datacard-lint's tests: every check on fixtures, the agents' own fixtures under the hive profile (632's
attribution-docs-check cases and 547's hf-dataset-card-lint case must still be caught), the Hub source against a fake
Hub, and the command line. Run: python -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

from datacard_lint.card import parse_card
from datacard_lint.checks import glob_regex, run_checks
from datacard_lint.cli import main
from datacard_lint.sources import HubSource, LocalSource, SourceError

FIX = pathlib.Path(__file__).parent / "fixtures"


def lint(folder, **kw):
    source = LocalSource(str(folder))
    text = source.readme()
    return run_checks(parse_card(text) if text is not None else None, source, **kw)


def ids(problems, severity=None):
    return sorted(p.id for p in problems if severity is None or p.severity == severity)


class Checks(unittest.TestCase):
    def test_a_strangers_template_card(self):
        problems = lint(FIX / "template_card")
        by = {}
        for p in problems:
            by.setdefault(p.id, []).append(p)
        self.assertEqual(ids(problems, "warning"), ["empty_link", "empty_section", "no_license", "placeholder", "placeholder"])
        self.assertEqual([p.message for p in by["placeholder"]], [
            "'Dataset Description' still has the template's [More Information Needed] (2 times)",
            "'Dataset Structure' still has the template's [More Information Needed]",
        ])
        self.assertEqual(by["empty_section"][0].message, "'Uses' has nothing under it", "a comment is not content")
        missing = sorted(p.message for p in by["missing_file"])
        self.assertEqual(missing, [
            "config 'default' names data_files 'data/test-*.csv', and no file in the dataset matches it",
            "the card links to a file 'paper.pdf' that is not in the dataset",
            "the card links to an image 'images/overview.png' that is not in the dataset",
        ])
        self.assertEqual([(p.file, p.line) for p in by["empty_link"]], [("data/train.csv", 3)])
        self.assertEqual(by["empty_link"][0].message, "column 'source' is empty in 1 of 2 row(s): line 3")
        self.assertEqual([(p.file, p.line) for p in by["ragged_rows"]], [("data/train.csv", 4)])
        self.assertNotIn("columns_mismatch", by, "its declared columns are the file's")
        self.assertNotIn("not_a_link", by, "a ragged row is not read for links")

    def test_a_clean_card_has_no_problems(self):
        self.assertEqual(lint(FIX / "clean_card"), [])

    def test_declared_columns_must_be_the_files_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(FIX / "clean_card", tmp, dirs_exist_ok=True)
            (pathlib.Path(tmp) / "data" / "part-1.csv").write_text("text,link\nhi,https://example.org\n")
            problems = lint(tmp)
        self.assertEqual(ids(problems), ["columns_mismatch"])
        self.assertEqual(problems[0].message, "config 'default': declared but not in the file: url; in the file but not declared: link")

    def test_header_problems(self):
        def header(text):
            with tempfile.TemporaryDirectory() as tmp:
                pathlib.Path(tmp, "README.md").write_text(text)
                return lint(tmp)
        self.assertEqual(ids(header("# Title\n\nText here.\n")), ["no_metadata"])
        self.assertEqual(ids(header("---\nlicense: mit\n# Title\n")), ["metadata_unclosed"])
        self.assertEqual(ids(header("---\nlicense: [mit\n---\n# T\n\nText.\n")), ["metadata_invalid"])
        self.assertEqual(ids(header("---\nlicense: other\n---\n# T\n\nText.\n")), ["license_unnamed"])
        self.assertEqual(ids(header("---\nlicense: other\nlicense_name: acme\n---\n# T\n\nText.\n")), [])
        self.assertEqual(ids(header("---\nlicense: mit\n---\n# T\n\n```\n## Not a heading\n[More Information Needed]\n```\n")), [], "code blocks are not sections")

    def test_links_that_leave_the_dataset_are_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "README.md").write_text("---\nlicense: mit\n---\n# T\n\n[a](https://x.org) [b](#t) [c](mailto:a@b.c) [d](./) [e](.gitattributes)\n")
            pathlib.Path(tmp, ".gitattributes").write_text("*.csv text\n")
            self.assertEqual(lint(tmp), [], "a leading dot is part of the name")


class LinkColumns(unittest.TestCase):
    """Found on the Hub, 2026-11-16: 'source' columns of names ("AWS Price List", "golf") are not link columns."""

    def card(self, csv_text):
        tmp = tempfile.mkdtemp()
        pathlib.Path(tmp, "README.md").write_text("---\nlicense: mit\n---\n# T\n\nText.\n")
        pathlib.Path(tmp, "d.csv").write_text(csv_text)
        return lint(tmp)

    def test_a_column_of_names_is_not_read_as_links(self):
        self.assertEqual(self.card("provider,source\naws,AWS Price List\ngcp,Google Cloud Billing Catalog API\nx,\n"), [])

    def test_a_column_of_links_with_odd_ones_out(self):
        problems = self.card("id,url\n1,https://a.org\n2,https://b.org\n3,b.org/c\n4,\n")
        self.assertEqual(sorted((p.id, p.severity, p.message) for p in problems), [
            ("empty_link", "warning", "column 'url' is empty in 1 of 4 row(s): line 5"),
            ("not_a_link", "error", "column 'url' is not a link, as most of its rows are in 1 row(s): line 4"),
        ])

    def test_hive_data_csv_keeps_632s_https_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(FIX / "hive_passing", tmp, dirs_exist_ok=True)
            pathlib.Path(tmp, "data.csv").write_text("step_id,topic,quote,source\n1,a,q,http://insecure.example\n2,b,q,Hub docs\n")
            errors = [p for p in lint(tmp, profile="hive") if p.severity == "error"]
            self.assertEqual([(p.id, p.message) for p in errors], [("not_a_link", "column 'source' is not an https link in 2 row(s): line 2, 3")])


class Coverage(unittest.TestCase):
    def test_a_huge_cell_reads_as_one_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "README.md").write_text("---\nlicense: mit\n---\n# T\n\nText.\n")
            pathlib.Path(tmp, "big.csv").write_text('a,b\n1,"' + "x" * 300_000 + '"\n')
            pathlib.Path(tmp, "bad.csv").write_bytes(b'a,b\n"1\x00,2\n')
            problems = lint(tmp)
        self.assertEqual([(p.id, p.file) for p in problems], [("ragged_rows", "bad.csv")], "a 300 KB cell is one cell; an unclosed quote is a short row")

    def test_no_card_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "d.csv").write_text("a\n1\n")
            problems = lint(tmp)
        self.assertEqual([(p.id, p.severity) for p in problems], [("no_card", "error")])

    def test_what_was_not_read_is_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "README.md").write_text("---\nlicense: mit\n---\n# T\n\nText.\n")
            for i in range(5):
                pathlib.Path(tmp, f"part-{i}.csv").write_text("a,b\n1,2,3\n")
            problems = lint(tmp, most_files=2)
        self.assertEqual(ids(problems), ["data_not_read", "ragged_rows", "ragged_rows"])
        note = next(p for p in problems if p.id == "data_not_read")
        self.assertEqual((note.severity, note.message[:44]), ("note", "3 of 5 CSV/TSV file(s) were not read (at mos"))


class AgentFixtures(unittest.TestCase):
    """The checks the HivemindOS agents built, as their own fixtures say."""

    def test_632_passing_card_passes_the_hive_layout(self):
        self.assertEqual(ids(lint(FIX / "hive_passing", profile="hive"), "error"), [])

    def test_632_failing_card_two_missing_sections_and_an_empty_source(self):
        problems = lint(FIX / "hive_failing", profile="hive")
        errors = [p for p in problems if p.severity == "error"]
        self.assertEqual(sorted((p.id, p.message) for p in errors), [
            ("empty_link", "column 'source' is empty in 1 of 1 row(s): line 2"),
            ("missing_section", "no 'Licence' section"),
            ("missing_section", "no 'What I could not check' section"),
        ])

    def test_547_ragged_row_and_632_misspelled_heading(self):
        problems = lint(FIX / "ragged", profile="hive")
        errors = sorted((p.id, p.message) for p in problems if p.severity == "error")
        self.assertEqual(errors, [
            ("heading_misspelled", "the 'Licence' section is spelled 'Licnse'"),
            ("ragged_rows", "1 row(s) do not have the header's 3 cells: line 3"),
        ], "547's 'What could not be checked' counts as the section")

    def test_hive_layout_needs_a_title_and_data_csv_with_a_source_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(FIX / "hive_passing", tmp, dirs_exist_ok=True)
            readme = pathlib.Path(tmp, "README.md")
            readme.write_text("Intro first.\n\n" + readme.read_text())
            pathlib.Path(tmp, "data.csv").write_text("step_id,topic\n1,x\n")
            self.assertEqual(ids(lint(tmp, profile="hive"), "error"), ["no_source_column", "no_title"])

    def test_a_required_section_from_the_command_line(self):
        self.assertEqual(ids(lint(FIX / "clean_card", require=["Citation"])), ["missing_section"])
        self.assertEqual(ids(lint(FIX / "clean_card", require=["Licence"])), [])


class Globs(unittest.TestCase):
    def test_hub_globs(self):
        cases = [
            ("data/*.csv", "data/a.csv", True), ("data/*.csv", "data/x/a.csv", False),
            ("data/**", "data/x/a.csv", True), ("**/*.parquet", "a.parquet", True), ("**/*.parquet", "x/y/a.parquet", True),
            ("data/train-0000?.csv", "data/train-00001.csv", True), ("data/[ab].csv", "data/c.csv", False),
            ("train.csv", "train.csv", True), ("train.csv", "x/train.csv", False),
        ]
        for pattern, path, ok in cases:
            self.assertEqual(bool(glob_regex(pattern).match(path)), ok, (pattern, path))


class Hub(unittest.TestCase):
    """The Hub source against a fake Hub that answers as the real one does, paging its file list with a Link header."""

    def hub(self, readme, files, data=None):
        asked = []

        def opener(url, headers):
            asked.append(url)
            if "/api/datasets/" in url:
                page = 2 if "cursor=" in url else 1
                entries = [{"type": "file", "path": p, "size": s} for p, s in files[: 2] if page == 1] + \
                          [{"type": "file", "path": p, "size": s} for p, s in files[2:] if page == 2] + \
                          [{"type": "directory", "path": "data"}]
                link = {"link": '<https://huggingface.co/api/datasets/o/n/tree/main?recursive=true&expand=false&cursor=abc>; rel="next"'} if page == 1 else {}
                return json.dumps(entries).encode(), link
            if url.endswith("/README.md"):
                return readme.encode(), {}
            for path, body in (data or {}).items():
                if url.endswith("/" + path):
                    return body.encode(), {}
            raise SourceError(f"{url} answered 404")
        return opener, asked

    def test_a_hub_dataset_is_read_and_checked(self):
        readme = (FIX / "template_card" / "README.md").read_text()
        csv = (FIX / "template_card" / "data" / "train.csv").read_text()
        opener, asked = self.hub(readme, [("README.md", 900), (".gitattributes", 10), ("data/train.csv", len(csv)), ("images/overview.png", 5)], {"data/train.csv": csv})
        source = HubSource("https://huggingface.co/datasets/o/n/tree/main", opener=opener, token="")
        self.assertEqual(source.repo_id, "o/n")
        self.assertEqual(sorted(source.files), [".gitattributes", "README.md", "data/train.csv", "images/overview.png"], "both pages")
        problems = run_checks(parse_card(source.readme()), source)
        self.assertEqual(sorted(p.message for p in problems if p.id == "missing_file"), [
            "config 'default' names data_files 'data/test-*.csv', and no file in the dataset matches it",
            "the card links to a file 'paper.pdf' that is not in the dataset",
        ], "the image is there on the Hub")
        self.assertIn("ragged_rows", ids(problems))
        self.assertTrue(all("huggingface.co" in url for url in asked))

    def test_a_large_data_file_is_not_downloaded(self):
        opener, asked = self.hub("---\nlicense: mit\n---\n# T\n\nText.\n", [("README.md", 40), ("big.csv", 21 * 1024 * 1024)])
        source = HubSource("o/n", opener=opener, token="")
        self.assertEqual(ids(run_checks(parse_card(source.readme()), source)), ["data_not_read"], "and it says so")
        self.assertFalse(any(url.endswith("big.csv") for url in asked))

    def test_links_to_its_own_files_on_the_hub_are_checked(self):
        readme = "---\nlicense: mit\n---\n# T\n\n![a](https://huggingface.co/datasets/o/n/resolve/main/img/a.png) ![b](https://huggingface.co/datasets/o/n/resolve/main/img/b.png)\n"
        opener, _ = self.hub(readme, [("README.md", 40), ("img/a.png", 3)])
        source = HubSource("hf://datasets/o/n", opener=opener, token="")
        self.assertEqual([p.message for p in run_checks(parse_card(source.readme()), source)], ["the card links to an image 'img/b.png' that is not in the dataset"])

    def test_a_listing_cut_short_calls_nothing_missing(self):
        # allenai/c4 has more files than are listed (2026-11-16): its configs' files were called missing.
        import datacard_lint.sources as sources
        readme = "---\nlicense: mit\nconfigs:\n- config_name: x\n  data_files: far/away-*.json\n---\n# T\n\nSee [it](far/away-1.json).\n"
        opener, _ = self.hub(readme, [("README.md", 40), ("a.json", 1), ("b.json", 1)])
        old, sources.LIST_MOST_PAGES = sources.LIST_MOST_PAGES, 1
        try:
            source = HubSource("o/n", opener=opener, token="")
        finally:
            sources.LIST_MOST_PAGES = old
        self.assertFalse(source.complete)
        problems = run_checks(parse_card(source.readme()), source)
        self.assertEqual([(p.id, p.severity) for p in problems], [("files_not_listed", "note")])

    def test_not_a_dataset_id(self):
        with self.assertRaises(SourceError):
            HubSource("not a/valid/id", opener=lambda u, h: (b"[]", {}), token="")


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes_and_text(self):
        code, out, _ = self.run_cli(str(FIX / "clean_card"))
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith(": ok (0 errors, 0 warnings)"))
        code, out, _ = self.run_cli(str(FIX / "template_card"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL missing_file (README.md:1): config 'default' names data_files", out)
        self.assertIn("WARN placeholder (README.md:", out)
        code, _, err = self.run_cli("not a/valid/id")
        self.assertEqual(code, 2, err)

    def test_warnings_fail_only_when_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "README.md").write_text("# T\n\nText.\n")
            self.assertEqual(self.run_cli(tmp)[0], 0)
            self.assertEqual(self.run_cli(tmp, "--strict")[0], 1)

    def test_json_and_github_formats(self):
        code, out, _ = self.run_cli(str(FIX / "hive_failing"), "--profile", "hive", "--format", "json")
        report = json.loads(out)
        self.assertEqual((code, report["ok"]), (1, False))
        self.assertEqual(sorted(p["id"] for p in report["problems"] if p["severity"] == "error"), ["empty_link", "missing_section", "missing_section"])
        _, out, _ = self.run_cli(str(FIX / "hive_failing"), "--profile", "hive", "--format", "github")
        self.assertIn("::error file=data.csv,line=2::datacard-lint empty_link: column 'source' is empty", out)
        self.assertIn("::error file=README.md::datacard-lint missing_section: no 'Licence' section", out)


if __name__ == "__main__":
    unittest.main()
