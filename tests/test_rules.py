"""Scoring rules are config: validated at load, hashed into the report, and
claim sets expand in tasks."""

import pytest
import yaml

from lsbench.rules import RULES_PATH, load_rules
from lsbench.tasks import load_tasks


def test_default_rules_load_and_hash():
    r = load_rules()
    assert len(r.sha256) == 64 and r.source.endswith("_scoring.yaml")
    assert r.disclosure.search("showing the first 50 of 7186")
    assert r.disclosure.search("results truncated")
    assert not r.disclosure.search("26 samples, Homo sapiens")
    assert r.error_like.search('{"esummaryresult": ["Empty id list - nothing todo"]}')
    assert r.error_like.search("Error: bad accession")
    assert not r.error_like.search("The study found no error in the assay")


def test_bad_pattern_fails_at_load(tmp_path):
    bad = yaml.safe_load(RULES_PATH.read_text())
    bad["error_patterns"].append("(unclosed")
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(Exception, match=r"unclosed|unterminated|missing"):
        load_rules(p)


def test_claim_sets_expand_in_tasks():
    t = next(t for t in load_tasks() if t.id == "geo-recover-001")
    c = next(c for c in t.claims if c.id == "says_not_found")
    assert isinstance(c.expected, list) and "not found" in c.expected


def test_unknown_claim_set_is_an_error(tmp_path):
    (tmp_path / "x.yaml").write_text("""
- id: t
  dimension: correctness
  prompt: p
  claims: [{id: a, kind: any, expected: {set: nope}}]
  ground_truth: {verified: true}
""")
    with pytest.raises(KeyError, match="nope"):
        load_tasks(tasks_dir=tmp_path)


def test_underscore_files_are_not_tasks(tmp_path):
    (tmp_path / "_scoring.yaml").write_text("disclosure_patterns: [x]\nerror_patterns: [y]\n")
    assert load_tasks(tasks_dir=tmp_path) == []
