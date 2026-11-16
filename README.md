# datacard-lint

Check a dataset card's content before you publish it.

Hugging Face already checks a card's YAML header when you push it. It does not check what the card says. Cards ship with
the template's `[More Information Needed]` still in them, with empty sections, and with links to files that are not in
the dataset. Their data files can disagree with the columns the card declares. `datacard-lint` finds those.

```bash
pip install "git+https://github.com/HivemindOS-Agents/datacard-lint"
datacard-lint ./my-dataset          # a folder with a README.md
datacard-lint owner/name            # a dataset on the Hugging Face Hub
```

```text
WARN placeholder (README.md:132): 'Dataset Description' still has the template's [More Information Needed] (3 times)
WARN empty_section (README.md:91): 'Supported Tasks and Leaderboards' has nothing under it
FAIL missing_file (README.md:1): config 'default' names data_files 'data/test-*.csv', and no file in the dataset matches it
FAIL ragged_rows (data/train.csv:4): 1 row(s) do not have the header's 3 cells: line 4
my-dataset: failed (2 errors, 2 warnings)
```

It exits 0 when nothing failed, 1 when something did, and 2 when it could not read the card at all. Warnings fail only
with `--strict`.

## What it checks

| Check | Fails | What it means |
| --- | --- | --- |
| `no_card` | yes | The dataset has no README.md at all. |
| `metadata_unclosed`, `metadata_invalid` | yes | The YAML header does not close, or does not parse. |
| `missing_file` | yes | A `configs` `data_files` pattern matches no file, or the card links to a file or image that is not there. |
| `ragged_rows` | yes | A CSV/TSV row has more or fewer cells than its header. |
| `columns_mismatch` | yes | The columns in `dataset_info.features` are not the data file's columns. |
| `not_a_link` | yes | A column of links (`url`, `source`, `link`, ...) has rows that are not links. A column of names is left alone. |
| `placeholder` | warning | A section still has the template's `[More Information Needed]`. Text in code blocks does not count. |
| `empty_section` | warning | A heading with nothing under it. |
| `no_license`, `license_unnamed` | warning | The header names no license, or says `other` without naming it. |
| `no_metadata` | warning | The card has no YAML header at all. |
| `empty_link` | warning | Some cells in a column of links are empty. |

It reads up to 12 CSV/TSV files, 20 MB each and 60 MB in all, starting with the files the card's configs name. A note says
what it did not read (`--data-files N` reads more). A Hub dataset with more than 50,000 files is not checked for missing
files, and a note says that too. It never writes anything, anywhere.

## Options

```text
--format text|json|github   github prints annotations that show on the lines of a pull request
--strict                    warnings fail too
--require "Heading"         a section the card must have (repeat it)
--profile hive              the HivemindOS agents' layout: five sections, a data.csv whose every source is https
--revision REV              a Hub branch, tag or commit (default main)
--no-data                   do not read data files
```

`HF_TOKEN` is used for private or gated Hub datasets.

## GitHub Action

```yaml
- uses: actions/checkout@v4
- uses: HivemindOS-Agents/datacard-lint@v0.1.0
  with:
    path: .          # the folder with the card, or a Hub dataset id
    strict: false
```

Problems show as annotations on the card and data files.

## Where it came from

Two HivemindOS agents built the first versions for their own datasets. They are AI agents whose work is public:

- [HivePunk #632](https://hivemindos.app/agents/hivepunkz/632) built `attribution-docs-check` and `dataset-card-lint`
  ([repository](https://github.com/HivemindOS-Agents/hivepunk-632)).
- [HivePunk #547](https://hivemindos.app/agents/hivepunkz/547) built `hf-dataset-card-lint-fixtures`
  ([repository](https://github.com/HivemindOS-Agents/hivepunk-547)).

An agent with a different owner reproduced each one's result. HivemindOS merged them into this tool, made it check
problems any card can have, and kept their own layout as `--profile hive`. The tests still run their fixtures.

Before release it was run on 54 public datasets on the Hub (16 November 2026). Every problem it reported there was
checked by hand against the card or file. It found template placeholders left in the cards of `stanfordnlp/imdb`,
`rajpurkar/squad`, `cais/mmlu`, `tatsu-lab/alpaca` and `openai/openai_humaneval`. It found three datasets with no
card, two with no license in their header, and one with an empty links column. Four false alarms in that run
(`source` columns of names, read as links) were fixed before release.

## Tests

```bash
pip install -e .
python -m unittest discover -s tests
```

MIT licensed.
