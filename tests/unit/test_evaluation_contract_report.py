from copy import deepcopy
from datetime import UTC, datetime

import pytest
from scripts.evaluation.contract_report import RANKING_RATES, RATES, make_report
from scripts.evaluation.systems import SYSTEMS


def measured():
    groups = []
    for split, count in (("train", 70), ("dev", 25), ("test", 25)):
        for system in SYSTEMS:
            rate = dict(numerator=0, denominator=0, value=None)
            groups.append(
                dict(
                    track="canonical",
                    category="all",
                    split=split,
                    variant=system,
                    ranking=dict(
                        episodes=count,
                        identifiable_faults=0,
                        mrr=None,
                        **{key: dict(rate) for key in RANKING_RATES},
                    ),
                    **{key: dict(rate) for key in RATES},
                    private_episode_ids=["never-export-this"],
                )
            )
    return dict(
        groups=groups,
        manifest=dict(
            origin="synthetic",
            identity=dict(
                generation="template",
                prepared_index_sha256="a" * 64,
                config=dict(model_hash="b" * 64),
                private_labels="never-export-this",
            ),
        ),
    )


def generate(data=None, completion=None):
    return make_report(
        data or measured(),
        completion or dict(episodes=120, status="synthetic_template_comparison_complete"),
        split_hash="c" * 64,
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
    )


def test_public_contract_uses_aggregates_and_explicit_nulls_only():
    report = generate()
    assert report.sample_size == 120
    assert report.published_at is None
    assert report.metrics["canonical.test.graph_learned.hit_at_1"] is None
    assert report.metrics["canonical.test.graph_learned.hit_at_1.denominator"] == 0
    assert "never-export-this" not in report.model_dump_json()
    assert report == generate()


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "rate", "count", "origin"])
def test_contract_export_rejects_incomplete_or_inconsistent_aggregates(mutation):
    data = deepcopy(measured())
    if mutation == "missing":
        data["groups"].pop()
    elif mutation == "duplicate":
        data["groups"].append(data["groups"][0])
    elif mutation == "rate":
        data["groups"][0]["citation_validity"]["value"] = 1
    elif mutation == "count":
        data["groups"][0]["ranking"]["episodes"] = 1
    else:
        data["manifest"]["origin"] = "external"
    with pytest.raises(ValueError):
        generate(data)


@pytest.mark.parametrize(
    "change",
    [dict(episodes=2), dict(smoke=True), dict(partial_selection=True), dict(status="running")],
)
def test_contract_export_refuses_partial_or_unfinished_run(change):
    with pytest.raises(ValueError, match="complete"):
        generate(
            completion=dict(episodes=120, status="synthetic_template_comparison_complete") | change
        )
