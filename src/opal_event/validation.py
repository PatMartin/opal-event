"""Helper for validating request bodies against a pydantic model chosen at runtime (used for
``ActionPatch``, whose concrete schema depends on the existing action's immutable ``type``)."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from opal_event.errors import ProblemError

ModelT = TypeVar("ModelT", bound=BaseModel)


class ValidationProblem(ProblemError):
    """A 422 error matching the shared OPAL ``HTTPValidationError`` shape."""

    def __init__(self, detail: list[dict[str, Any]]):
        super().__init__(422, "validation_error", "Request validation error")
        self.detail = detail

    @classmethod
    def from_pydantic(cls, exc: ValidationError) -> ValidationProblem:
        detail = [{"loc": [str(part) for part in err["loc"]], "msg": err["msg"], "type": err["type"]} for err in exc.errors()]
        return cls(detail)

    @classmethod
    def from_message(cls, loc: list[str], msg: str, type_: str = "value_error") -> ValidationProblem:
        return cls([{"loc": loc, "msg": msg, "type": type_}])


def validate_model(model_cls: type[ModelT], data: Any) -> ModelT:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ValidationProblem.from_pydantic(exc) from exc


def validate_type(type_: Any, data: Any) -> Any:
    """Validates ``data`` against any pydantic-compatible type, including discriminated unions
    (e.g. ``Action``) that are not themselves ``BaseModel`` subclasses."""
    try:
        return TypeAdapter(type_).validate_python(data)
    except ValidationError as exc:
        raise ValidationProblem.from_pydantic(exc) from exc
