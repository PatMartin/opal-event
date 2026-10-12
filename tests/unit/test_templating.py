import pytest

from opal_event.templating import TemplateError, render_value


class TestRenderValue:
    def test_whole_value_preserves_type(self):
        event = {"data": {"count": 5, "flag": True, "obj": {"a": 1}}}
        assert render_value("{{data.count}}", event) == 5
        assert render_value("{{data.flag}}", event) is True
        assert render_value("{{data.obj}}", event) == {"a": 1}

    def test_interpolated_string_stringifies(self):
        event = {"data": {"camera": "front_door", "count": 3}}
        assert render_value("camera={{data.camera}} count={{data.count}}", event) == "camera=front_door count=3"

    def test_whole_event_placeholder(self):
        event = {"topic": "t", "data": {"a": 1}}
        assert render_value("{{event}}", event) == event

    def test_nested_structures(self):
        event = {"data": {"camera": "front_door"}}
        template = {"a": "{{data.camera}}", "b": ["{{data.camera}}", 1]}
        assert render_value(template, event) == {"a": "front_door", "b": ["front_door", 1]}

    def test_missing_path_raises(self):
        with pytest.raises(TemplateError):
            render_value("{{data.missing}}", {"data": {}})

    def test_unsupported_root_raises(self):
        with pytest.raises(TemplateError):
            render_value("{{other}}", {"data": {}})

    def test_non_template_values_pass_through(self):
        assert render_value(42, {}) == 42
        assert render_value(None, {}) is None
