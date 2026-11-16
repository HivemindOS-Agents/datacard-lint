---
language:
- en
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train.csv
  - split: test
    path: data/test-*.csv
dataset_info:
  features:
  - name: text
    dtype: string
  - name: label
    dtype: int64
  - name: source
    dtype: string
---

# Dataset Card for Movie Reviews

![overview](images/overview.png)

## Dataset Details

### Dataset Description

Short reviews of films, each with a sentiment label.

- **Curated by:** [More Information Needed]
- **License:** [More Information Needed]

## Uses

<!-- Address questions around how the dataset is intended to be used. -->

## Dataset Structure

[More Information Needed]

## Citation

See [the paper](paper.pdf) and [the website](https://example.org/reviews).
