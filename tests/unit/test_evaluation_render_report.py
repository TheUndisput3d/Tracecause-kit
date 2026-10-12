import pytest
from scripts.evaluation.render_report import captured_section


def test_captured_report_preserves_undefined_denominators():
    empty = dict(numerator=0, denominator=0, value=None)
    metrics = dict(
        groups=[
            dict(
                track="canonical",
                category="all",
                split="test",
                variant="graph_learned",
                ranking=dict(identifiable_faults=0, hit_at_1=empty),
                exposed_hit1_identifiable=empty,
                answered_coverage=dict(numerator=0, denominator=6, value=0),
            )
        ]
    )
    result = "\n".join(
        captured_section(metrics, dict(status="captured_transfer_complete", episodes=24, runs=120))
    )
    assert "undefined (n=0) | undefined (n=0) | 0/6" in result
    assert "not correctness" in result


def test_captured_report_refuses_partial_benchmark():
    with pytest.raises(ValueError, match="complete"):
        captured_section({}, dict(status="running", episodes=23))
