from typing import Any, NoReturn
from warnings import deprecated

from pydantic import BaseModel
from sqlalchemy.orm import DeclarativeBase

from shared.service.service import Put, Service


class ReadOnlyViewError(NotImplementedError):
    """Raised when a write is attempted through a `ViewService`.

    A SQL view has no writable backing table, so this fails fast with a
    clear message instead of surfacing as a generic "view is not
    writable" error from deep inside a flush.
    """


class ViewService[RowT: DeclarativeBase, SchemaT: BaseModel](
    Service[RowT, SchemaT], abstract=True
):
    """A `Service` over a read-only SQL view. `put`/`create`/`update`/
    `delete` always raise `ReadOnlyViewError` — use `get`/`search`/
    `search_partial` instead.

    Each is also `@deprecated`, so a static type checker flags any call
    site before it ever runs.
    """

    def _read_only(self) -> NoReturn:
        raise ReadOnlyViewError(
            f"{type(self).__name__} is backed by a read-only view; "
            f"use get()/search()/search_partial() instead."
        )

    @deprecated("Views are read-only: put() always raises ReadOnlyViewError.")
    def put(
        self, id: Any | None, data: BaseModel | dict[str, Any], *, partial: bool = False
    ) -> Put[SchemaT]:
        self._read_only()

    @deprecated("Views are read-only: create() always raises ReadOnlyViewError.")
    def create(self, data: BaseModel | dict[str, Any]) -> SchemaT:
        self._read_only()

    @deprecated("Views are read-only: update() always raises ReadOnlyViewError.")
    def update(self, id: Any, data: BaseModel | dict[str, Any]) -> SchemaT | None:
        self._read_only()

    @deprecated("Views are read-only: delete() always raises ReadOnlyViewError.")
    def delete(self, id: Any) -> bool:
        self._read_only()
