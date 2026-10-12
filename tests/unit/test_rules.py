from opal_event.models import Condition, Rule
from opal_event.rules import evaluate_condition, evaluate_rule, topic_matches


class TestTopicMatches:
    def test_exact_match(self):
        assert topic_matches("vision", "vision")
        assert not topic_matches("vision", "vision.object")

    def test_prefix_wildcard_matches_descendants(self):
        assert topic_matches("vision.*", "vision.object.detected")
        assert topic_matches("vision.*", "vision.object")

    def test_prefix_wildcard_excludes_bare_and_similar_prefix(self):
        assert not topic_matches("vision.*", "vision")
        assert not topic_matches("vision.*", "visionary")


class TestEvaluateCondition:
    def test_eq(self):
        c = Condition(path="data.object", operator="eq", value="person")
        assert evaluate_condition(c, {"data": {"object": "person"}})
        assert not evaluate_condition(c, {"data": {"object": "cat"}})

    def test_missing_path_is_false_except_exists_false(self):
        c = Condition(path="data.missing", operator="eq", value=1)
        assert not evaluate_condition(c, {"data": {}})
        exists_false = Condition(path="data.missing", operator="exists", value=False)
        assert evaluate_condition(exists_false, {"data": {}})
        exists_true = Condition(path="data.missing", operator="exists", value=True)
        assert not evaluate_condition(exists_true, {"data": {}})

    def test_numeric_comparisons_require_numbers(self):
        c = Condition(path="data.confidence", operator="gt", value=0.5)
        assert evaluate_condition(c, {"data": {"confidence": 0.9}})
        assert not evaluate_condition(c, {"data": {"confidence": "0.9"}})
        bool_c = Condition(path="data.flag", operator="gt", value=0.5)
        assert not evaluate_condition(bool_c, {"data": {"flag": True}})

    def test_in_not_in(self):
        c_in = Condition(path="data.object", operator="in", value=["person", "car"])
        assert evaluate_condition(c_in, {"data": {"object": "person"}})
        c_not_in = Condition(path="data.object", operator="not_in", value=["person", "car"])
        assert evaluate_condition(c_not_in, {"data": {"object": "cat"}})

    def test_contains_string_and_list(self):
        c_str = Condition(path="data.label", operator="contains", value="son")
        assert evaluate_condition(c_str, {"data": {"label": "person"}})
        c_list = Condition(path="data.tags", operator="contains", value="x")
        assert evaluate_condition(c_list, {"data": {"tags": ["x", "y"]}})

    def test_array_index_path(self):
        c = Condition(path="data.items.0", operator="eq", value="first")
        assert evaluate_condition(c, {"data": {"items": ["first", "second"]}})


class TestEvaluateRule:
    def test_rule_requires_topic_and_all_conditions(self):
        rule = Rule(
            id="r1",
            topic="vision.*",
            conditions=[Condition(path="data.object", operator="eq", value="person")],
            actions=["a1"],
        )
        matched, checks = evaluate_rule(rule, {"topic": "vision.object.detected", "data": {"object": "person"}})
        assert matched
        assert any(c["kind"] == "topic" for c in checks)

        matched2, _ = evaluate_rule(rule, {"topic": "vision.object.detected", "data": {"object": "cat"}})
        assert not matched2

    def test_disabled_rule_never_matches(self):
        rule = Rule(id="r1", enabled=False, topic="vision.*", conditions=[], actions=["a1"])
        matched, _ = evaluate_rule(rule, {"topic": "vision.anything", "data": {}})
        assert not matched
