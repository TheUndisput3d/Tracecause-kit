from copy import deepcopy

import pytest
from scripts.evaluation.graph_projection import verify_parent_support


def fixture():
    cache = {
        "hash": {
            ("trace", "child"): dict(
                trace_id="trace",
                span_id="child",
                parent_span_id="parent",
                kind="SERVER",
                service_key="callee",
                start_ns="10",
                end_ns="20",
            ),
            ("trace", "parent"): dict(
                trace_id="trace",
                span_id="parent",
                parent_span_id=None,
                kind="CLIENT",
                service_key="caller",
                start_ns="5",
                end_ns="25",
            ),
        }
    }
    observation = dict(
        type="dependency",
        sample_count=1,
        caller_service_key="caller",
        callee_service_key="callee",
        supports=[
            dict(trace_id="trace", span_id=key, role=role, artifact_hash="hash")
            for key, role in (("child", "measurement"), ("parent", "ancestor"))
        ],
    )
    return observation, cache


def test_direct_and_same_service_parent_chains():
    obs, cache = fixture()
    assert verify_parent_support(obs, cache) == 1
    intermediate = deepcopy(cache["hash"][("trace", "child")])
    intermediate.update(span_id="middle", kind="INTERNAL")
    cache["hash"][("trace", "middle")] = intermediate
    cache["hash"][("trace", "child")]["parent_span_id"] = "middle"
    obs["supports"].append(
        dict(trace_id="trace", span_id="middle", role="ancestor", artifact_hash="hash")
    )
    assert verify_parent_support(obs, cache) == 1


@pytest.mark.parametrize(
    "change",
    ["unrelated", "wrong_trace", "cycle", "async", "time", "caller", "callee", "count", "kind"],
)
def test_reject_invalid_call_witness(change):
    obs, cache = fixture()
    child, parent = cache["hash"][("trace", "child")], cache["hash"][("trace", "parent")]
    if change == "unrelated":
        child["parent_span_id"] = "uncited"
    elif change == "wrong_trace":
        cache["hash"][("different", "parent")] = cache["hash"].pop(("trace", "parent"))
        obs["supports"][1]["trace_id"] = "different"
    elif change == "cycle":
        child["parent_span_id"] = "child"
    elif change == "async":
        parent["kind"] = "PRODUCER"
    elif change == "time":
        parent.update(start_ns="30", end_ns="40")
    elif change == "caller":
        parent["service_key"] = "other"
    elif change == "callee":
        child["service_key"] = "other"
    elif change == "count":
        obs["sample_count"] = 2
    elif change == "kind":
        child["kind"] = "CLIENT"
    with pytest.raises(ValueError):
        verify_parent_support(obs, cache)
