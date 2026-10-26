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

    def test_eq_and_ne_distinguish_booleans_from_numbers(self):
        assert not evaluate_condition(Condition(path="data.value", operator="eq", value=1), {"data": {"value": True}})
        assert evaluate_condition(Condition(path="data.value", operator="ne", value=1), {"data": {"value": True}})
        assert not evaluate_condition(Condition(path="data.value", operator="eq", value=0), {"data": {"value": False}})
        assert evaluate_condition(Condition(path="data.value", operator="ne", value=0), {"data": {"value": False}})
        assert evaluate_condition(Condition(path="data.value", operator="eq", value=1.0), {"data": {"value": 1}})
        assert not evaluate_condition(Condition(path="data.value", operator="ne", value=1.0), {"data": {"value": 1}})

    def test_eq_and_ne_distinguish_nested_booleans_from_numbers(self):
        actual = {"data": {"value": [True, {"enabled": False}]}}
        assert not evaluate_condition(Condition(path="data.value", operator="eq", value=[1, {"enabled": False}]), actual)
        assert evaluate_condition(Condition(path="data.value", operator="ne", value=[1, {"enabled": False}]), actual)

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


class TestJsonMembership:
    """Membership operators (in/not_in/list-contains) use JSON-aware equality: bools are not
    numbers, int/float are numerically equivalent, null is a real member, structures compare
    recursively."""

    def _in(self, value, options):
        return evaluate_condition(Condition(path="data.x", operator="in", value=options), {"data": {"x": value}})

    def test_true_is_not_1(self):
        assert not self._in(True, [1])
        assert self._in(True, [True])

    def test_false_is_not_0(self):
        assert not self._in(False, [0])
        assert self._in(False, [False])

    def test_int_matches_float(self):
        assert self._in(1, [1.0, 2.5])
        assert self._in(1.0, [1])

    def test_null_membership(self):
        assert self._in(None, [None])
        assert not self._in(None, [0, False])
        assert not self._in(0, [None])

    def test_ordinary_membership(self):
        assert self._in("a", ["a", "b"])
        assert not self._in("c", ["a", "b"])
        assert not self._in("a", "not-a-list")  # non-list value rejects

    def test_not_in(self):
        cond = lambda v, opts: evaluate_condition(  # noqa: E731
            Condition(path="data.x", operator="not_in", value=opts), {"data": {"x": v}}
        )
        assert cond(True, [1])  # True is NOT in [1] under JSON typing
        assert not cond(1, [1.0, 2])
        assert cond("z", ["a", "b"])

    def test_contains_nested_structures(self):
        ev = {"data": {"items": [{"a": 1}, [1, 2], "x"]}}
        assert evaluate_condition(Condition(path="data.items", operator="contains", value={"a": 1}), ev)
        assert evaluate_condition(Condition(path="data.items", operator="contains", value=[1, 2]), ev)
        assert not evaluate_condition(Condition(path="data.items", operator="contains", value={"a": 1.5}), ev)
        assert not evaluate_condition(Condition(path="data.items", operator="contains", value=[1, "2"]), ev)

    def test_contains_keeps_bool_number_distinct(self):
        ev = {"data": {"items": [1, 0]}}
        assert not evaluate_condition(Condition(path="data.items", operator="contains", value=True), ev)
        assert not evaluate_condition(Condition(path="data.items", operator="contains", value=False), ev)
        ev2 = {"data": {"items": [True]}}
        assert evaluate_condition(Condition(path="data.items", operator="contains", value=True), ev2)

    def test_contains_string_unchanged(self):
        assert evaluate_condition(Condition(path="data.s", operator="contains", value="ell"), {"data": {"s": "hello"}})
        assert not evaluate_condition(Condition(path="data.s", operator="contains", value="bye"), {"data": {"s": "hello"}})

    def test_membership_missing_path(self):
        assert not evaluate_condition(Condition(path="data.missing", operator="in", value=[1]), {"data": {}})
        assert not evaluate_condition(Condition(path="data.missing", operator="contains", value=1), {"data": {}})
