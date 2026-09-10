"""
db/service.py — per-entity services over a generic, typed base.

Design intent: consumers of a service never see an ORM row, a session, or
a SQL expression. They call `get`, `find`, `create` and get Pydantic
models back. Everything that would let a caller build arbitrary SQL is
protected (leading underscore) and available only to subclasses.

    class UserService(Service[UserRow, User]):
        pass

    users = UserService(session)
    users.get(1)              # -> User | None
    users.list_all()          # -> list[User]
    users.find(name="ada")    # -> list[User]   (see UserService below)

Two type parameters, not one: `UserRow` is the mapped table the service
queries, `User` is the Pydantic schema it returns. Keeping them separate
is what lets your API schemas stay independent of table shape and keep
validating request bodies.

The type arguments *are* the declaration — there is no `model = User`
class attribute to keep in sync. `__init_subclass__` recovers them from
`Service[UserRow, User]` at class-creation time.

Python 3.14+ (PEP 695 generics, PEP 649 deferred annotations).
Requires: sqlalchemy>=2.0.49, pydantic>=2.0
"""

from abc import ABC
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import get_original_bases
from typing import Any, ClassVar, Literal, Optional, Self, TypeVar, get_args

from pydantic import BaseModel, create_model
from sqlalchemy import (
    ColumnElement,
    Row,
    Select,
    and_,
    event,
    func,
    inspect,
    not_,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Session


def configure_sqlite(engine: Engine) -> Engine:
    """Make SQLite safe for `Service.put`. Call once, on the engine.

    `put` relies on SAVEPOINT (`Session.begin_nested`) to recover from a
    duplicate-key insert. On SQLite that does not work out of the box:
    the pysqlite driver defers BEGIN until it sees DML, and `put` reaches
    the savepoint after only a SELECT. The savepoint is then created
    outside any transaction, so its writes commit immediately and an
    outer `session.rollback()` will not undo them.

    This is measurable rather than theoretical. Same statement sequence,
    default driver settings versus these:

        default (legacy)   in_transaction=False  rollback leaves the row
        configured here    in_transaction=True   rollback removes the row

    Also enables two pragmas that are not SQLAlchemy's doing: foreign key
    enforcement, which SQLite leaves OFF per connection, and WAL, which
    lets readers proceed during a write.

    Not needed for Postgres or MySQL.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Stop pysqlite emitting (and deferring) its own BEGIN.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # no-op for :memory:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    return engine


type Predicate = ColumnElement[bool]
"""Anything usable in a WHERE clause: a comparison, and_(), or_(), .in_()."""


class ServiceDefinitionError(TypeError):
    """Raised at class-creation time when a service is declared wrongly."""


class RowHiddenError(LookupError):
    """A row with this key exists but `_base_query()` filters it out.

    Raised by `put` rather than silently attempting an insert that would
    collide on the primary key. Most often this means the row is soft
    deleted.
    """


class Outcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"


class Op(StrEnum):
    """Operators a consumer is allowed to ask for.

    A closed set, deliberately. This is the whole reason `search` can be
    generic without handing out a `Select`: callers describe what they
    want in this vocabulary, and the service decides how to compile it.
    """

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    IN = "in"
    LIKE = "like"
    IS_NULL = "is_null"
    BETWEEN = "between"
    RANGE = "range"


class Junction(StrEnum):
    AND = "and"
    OR = "or"


class Combinable:
    """Boolean composition for filter nodes.

    `&`, `|` and `~` build a tree of plain values. Nothing is compiled
    until a service walks it, so the tree stays inspectable, comparable
    and safe to accept from outside.

        (Criterion.eq("a", 1) & Criterion.eq("b", 2)) | Criterion.eq("c", 3)

    Python binds `&` tighter than `|`, the same way SQL binds AND tighter
    than OR, so `a & b | c` means `(a AND b) OR c` as you would expect.
    Note this is *unlike* raw SQLAlchemy, where you must parenthesise
    every comparison because `==` binds looser than `&`. Criteria are
    built by constructors here, so that hazard does not arise.
    """

    def __and__(self, other: "Filter") -> "Group":
        return _combine(Junction.AND, self, other)  # type: ignore[arg-type]

    def __or__(self, other: "Filter") -> "Group":
        return _combine(Junction.OR, self, other)  # type: ignore[arg-type]

    def __invert__(self) -> "Not":
        return Not(self)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Criterion(Combinable):
    """One filter condition, as data rather than as SQL."""

    field: str
    value: Any = None
    op: Op = Op.EQ

    @classmethod
    def eq(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.EQ)

    @classmethod
    def ne(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.NE)

    @classmethod
    def gt(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.GT)

    @classmethod
    def gte(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.GTE)

    @classmethod
    def lt(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.LT)

    @classmethod
    def lte(cls, field: str, value: Any) -> "Criterion":
        return cls(field, value, Op.LTE)

    @classmethod
    def in_(cls, field: str, values: Sequence[Any]) -> "Criterion":
        return cls(field, tuple(values), Op.IN)

    @classmethod
    def like(cls, field: str, pattern: str) -> "Criterion":
        return cls(field, pattern, Op.LIKE)

    @classmethod
    def is_null(cls, field: str, null: bool = True) -> "Criterion":
        return cls(field, null, Op.IS_NULL)

    @classmethod
    def between(cls, field: str, lower: Any = None, upper: Any = None) -> "Criterion":
        """Inclusive on both ends: `lower <= x <= upper`.

        Matches SQL BETWEEN. Correct for discrete values like integer
        ages or scores. For timestamps prefer `range_`, since an
        inclusive upper bound of a date excludes that day's times — see
        the note on `Op.RANGE`.

        Either bound may be None for a one-sided comparison, which is
        what you want when `min`/`max` arrive as optional query params.
        """
        return cls(field, (lower, upper), Op.BETWEEN)

    @classmethod
    def range_(cls, field: str, lower: Any = None, upper: Any = None) -> "Criterion":
        """Half-open: `lower <= x < upper`.

        The right default for dates and timestamps. "January" is
        `range_("at", jan_1, feb_1)`, which cannot miss a time on the
        31st the way an inclusive upper bound of the 31st does.

        Either bound may be None for a one-sided comparison.
        """
        return cls(field, (lower, upper), Op.RANGE)


@dataclass(frozen=True, slots=True)
class Group(Combinable):
    """Several filters joined by AND or OR."""

    junction: Junction
    terms: tuple["Filter", ...]


@dataclass(frozen=True, slots=True)
class Not(Combinable):
    """Negation of a filter.

    Careful with nullable columns: `NOT (x = 5)` does not match rows
    where x IS NULL, because NULL comparisons are unknown rather than
    false. If you want those rows, say so explicitly:

        ~Criterion.eq("x", 5) | Criterion.is_null("x")
    """

    term: "Filter"


type Filter = Criterion | Group | Not
"""A filter tree. Leaves are Criterion; Group and Not are the branches."""


def all_of(*terms: Filter) -> Group:
    """AND several filters. Equivalent to chaining `&`."""
    return Group(Junction.AND, terms)


def any_of(*terms: Filter) -> Group:
    """OR several filters. Equivalent to chaining `|`."""
    return Group(Junction.OR, terms)


def _combine(junction: Junction, left: Filter, right: Filter) -> Group:
    """Join two nodes, flattening same-junction groups.

    Keeps `a & b & c` as one three-term AND rather than nesting it, so
    the compiled SQL and the depth limit both reflect the intent.
    """
    terms: list[Filter] = []
    for side in (left, right):
        if isinstance(side, Group) and side.junction is junction:
            terms.extend(side.terms)
        else:
            terms.append(side)
    return Group(junction, tuple(terms))


def _shape(node: Filter) -> tuple[int, int]:
    """Return (depth, node_count) for a filter tree."""
    match node:
        case Criterion():
            return 1, 1
        case Not(term=inner):
            depth, count = _shape(inner)
            return depth + 1, count + 1
        case Group(terms=terms):
            if not terms:
                return 1, 1
            sub = [_shape(t) for t in terms]
            return max(d for d, _ in sub) + 1, sum(c for _, c in sub) + 1
        case _:
            raise TypeError(f"not a filter node: {node!r}")


class Direction(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class Sort:
    field: str
    direction: Direction | Literal["asc", "desc"] = Direction.ASC


def filters_model[SchemaT: BaseModel](
    entity: type[SchemaT],
    *,
    exclude: Collection[str] = (),
    name: str | None = None,
) -> type[BaseModel]:
    """Derive an all-optional filter model from an entity schema.

    The fields are read off `entity`, so a column added there becomes
    filterable without touching the route that accepts this model. Feed
    the result to `Service.search_partial`.

        PlayerFilters = filters_model(Player, exclude={"first_name_norm"})

    `exclude` keeps internal columns off a public API surface, which is
    why this is not simply `pydantic_partial.create_partial_model` —
    that makes every field optional but keeps all of them.
    """
    fields: dict[str, Any] = {}
    for field, info in entity.model_fields.items():
        if field in exclude:
            continue
        annotation: Any = info.annotation if info.annotation is not None else Any
        fields[field] = (Optional[annotation], None)
    return create_model(name or f"{entity.__name__}Filters", **fields)


def _column_value(value: Any) -> Any:
    """An enum as the string its column actually holds.

    `NFLTeam.BUFFALO_BILLS.value` is the tuple `("Buffalo Bills", "BUF")`
    while the column stores `"BUF"` — which is what `str()` yields. A
    StrEnum is already a str and passes through untouched.
    """
    if isinstance(value, Enum) and not isinstance(value, str):
        return str(value)
    return value


@dataclass(frozen=True, slots=True)
class Page:
    """Pagination request. `limit` is clamped by `Service.max_limit`."""

    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class Results[SchemaT: BaseModel]:
    """A page of results plus the total, which APIs almost always need."""

    items: list[SchemaT]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class Put[SchemaT: BaseModel]:
    """Result of `Service.put`: the row, and which branch was taken.

    Callers usually need this to choose an HTTP status, so `put` reports
    it rather than collapsing both cases into a bare model.
    """

    value: SchemaT
    outcome: Outcome

    @property
    def created(self) -> bool:
        return self.outcome is Outcome.CREATED

    @property
    def updated(self) -> bool:
        return self.outcome is Outcome.UPDATED

    @property
    def status_code(self) -> int:
        """201 for a create, 200 for an update."""
        return 201 if self.created else 200


class Service[RowT: DeclarativeBase, SchemaT: BaseModel](ABC):
    """Generic base holding the logic every entity service shares.

    Subclasses override only what differs. The public methods below are
    written in terms of `_base_query()`, so overriding that one hook
    changes every query the service can run — which is the main reason
    this is a class hierarchy rather than a set of functions.
    """

    # Annotation-only declarations. `__init_subclass__` assigns these.
    # Deliberately not ClassVar: PEP 526 forbids type variables inside
    # ClassVar, and type checkers reject `ClassVar[type[RowT]]`.
    row: type[RowT]
    schema: type[SchemaT]

    #: Set True on an intermediate base that should stay generic.
    __abstract_service__: ClassVar[bool] = False

    #: Fields `search` will filter on. None means every mapped column.
    #: Narrow this to keep unindexed or private columns out of reach —
    #: consumers cannot filter on `password_hash` if it is not listed.
    filterable: ClassVar[frozenset[str] | None] = None

    #: Fields `search` will sort on. None means every mapped column.
    sortable: ClassVar[frozenset[str] | None] = None

    #: Ceiling on `Page.limit`, so a caller cannot ask for everything.
    max_limit: ClassVar[int] = 200

    #: How deeply a filter tree may nest.
    max_filter_depth: ClassVar[int] = 8

    #: How many nodes a filter tree may contain in total.
    max_filter_terms: ClassVar[int] = 50

    # -- declaration-time wiring -------------------------------------------

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.__abstract_service__ = abstract
        if abstract:
            return

        for base in get_original_bases(cls):
            args = get_args(base)
            if len(args) == 2 and not any(isinstance(a, TypeVar) for a in args):
                cls.row, cls.schema = args
                break
        else:
            # No concrete arguments found. Either this is a still-generic
            # intermediate (declare it with `abstract=True`) or the author
            # forgot the parameters.
            if not hasattr(cls, "row"):
                raise ServiceDefinitionError(
                    f"{cls.__name__} must be declared as "
                    f"Service[SomeRow, SomeSchema], or pass abstract=True "
                    f"if it is an intermediate base."
                )
            return

        # Fail loudly now rather than on the first query.
        if not cls.schema.model_config.get("from_attributes"):
            raise ServiceDefinitionError(
                f"{cls.schema.__name__} needs "
                f"model_config = ConfigDict(from_attributes=True) to be "
                f"built from ORM rows."
            )

    def __init__(self, session: Session) -> None:
        if self.__abstract_service__ or not hasattr(self, "row"):
            raise ServiceDefinitionError(
                f"{type(self).__name__} is abstract and cannot be instantiated."
            )
        self.session = session

    # ======================================================================
    # Extension hooks — override these in a subclass
    # ======================================================================

    def _base_query(self) -> Select[tuple[RowT]]:
        """The starting point for every read this service performs.

        Override to apply a scope that must never be forgotten:

            class UserService(Service[UserRow, User]):
                def _base_query(self):
                    return super()._base_query().where(
                        UserRow.deleted_at.is_(None)
                    )

        Because `get`, `find`, `count` and `list_all` are all built on
        this, one override covers them all.
        """
        return select(self.row)

    def _to_schema(self, row: RowT) -> SchemaT:
        """Row -> DTO. Override for computed or derived fields.

        `model_validate` re-validates each row. On a hot path where the
        database is already trusted, `self.schema.model_construct(...)`
        skips validation — measure before reaching for it.
        """
        return self.schema.model_validate(row)

    def _criterion_for(self, field: str, value: Any) -> Filter:
        """One filter-model field -> one filter node. Override per entity.

        The default is equality against the identically named column.
        Override when an input needs normalising, or belongs to a
        different column than the one it is named after:

            def _criterion_for(self, field, value):
                if field == "last_name":
                    return Criterion.like(
                        "last_name_norm", f"%{clean_name(value)}%"
                    )
                return super()._criterion_for(field, value)

        This is the hook that keeps entity-specific query logic on the
        entity's service instead of spread across its routes.
        """
        return Criterion.eq(field, _column_value(value))

    def _may_update_hidden(self, row: RowT) -> bool:
        """Whether `put` may update a row that `_base_query()` hides.

        Default is no, which turns a would-be primary key collision into
        an explicit `RowHiddenError`. Override to resurrect instead:

            def _may_update_hidden(self, row):
                row.deleted_at = None
                return True
        """
        return False

    # ======================================================================
    # Public API — what consumers are allowed to call
    # ======================================================================

    def get(self, id: Any) -> SchemaT | None:
        """Fetch by primary key. Respects `_base_query()`."""
        row = self.session.scalars(
            self._base_query().where(self._pk() == id)
        ).one_or_none()
        return None if row is None else self._to_schema(row)

    def get_or_raise(self, id: Any) -> SchemaT:
        found = self.get(id)
        if found is None:
            raise LookupError(f"{self.row.__name__} {id!r} not found")
        return found

    def list_all(self, *, limit: int | None = None, offset: int = 0) -> list[SchemaT]:
        stmt = self._base_query().offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return self._rows_to_schemas(stmt)

    def count(self) -> int:
        return self._count()

    def search(
        self,
        *criteria: Filter,
        sort: Sequence[Sort] | Sort = (),
        page: Page | None = None,
    ) -> Results[SchemaT]:
        """The generic query method. Returns DTOs, never rows.

            users.search(
                Criterion.like("name", "ad%"),
                Criterion.gte("age", 18),
                sort=[Sort("name", Direction.DESC)],
                page=Page(limit=20),
            )

        Criteria are ANDed. This is a `Select` you cannot hold: the
        service compiles your description against `_base_query()`, so
        every scope that hook applies still applies here. For OR or
        anything else this vocabulary cannot express, add a method to the
        subclass built on `_where()`.
        """
        page = page or Page()
        conditions = [self._compile(c) for c in criteria]

        stmt = self._base_query()
        if conditions:
            stmt = stmt.where(*conditions)
        if isinstance(sort, Sort):
            sort = [sort]

        for spec in sort:
            stmt = stmt.order_by(self._compile_sort(spec))

        limit = max(1, min(page.limit, self.max_limit))
        offset = max(0, page.offset)
        print(stmt)
        print(stmt.compile())
        print(stmt.compile().params)
        return Results(
            items=self._rows_to_schemas(stmt.limit(limit).offset(offset)),
            total=self._count(*conditions),
            limit=limit,
            offset=offset,
        )

    def search_partial(
        self,
        filters: BaseModel,
        *criteria: Filter,
        sort: Sequence[Sort] = (),
        page: Page | None = None,
    ) -> Results[SchemaT]:
        """`search`, driven by a partial of the schema.

            games.search_partial(GameFilters(week=3), page=Page(limit=20))

        Every field the caller actually set becomes one filter node via
        `_criterion_for`; fields left unset — or set to None — are
        ignored. That is what replaces a chain of `if param is not None`
        branches at the call site with a single loop.

        Positional `criteria` are ANDed in afterwards, for query params
        that are not fields of the entity at all. See
        `GameService.played_in` for one.
        """
        described = [
            self._criterion_for(field, value)
            for field in sorted(filters.model_fields_set)
            if (value := getattr(filters, field)) is not None
        ]
        return self.search(*described, *criteria, sort=sort, page=page)

    def exists(self, id: Any) -> bool:
        stmt = select(self._base_query().where(self._pk() == id).exists())
        return bool(self.session.scalar(stmt))

    def put(
        self,
        id: Any | None,
        data: BaseModel | dict[str, Any],
        *,
        partial: bool = False,
    ) -> Put[SchemaT]:
        """Create if absent, update if present. Reports which happened.

            result = users.put(1, payload)
            result.value      # -> User
            result.created    # -> bool

        `id` may be None, in which case it is taken from `data` if the
        payload carries a primary key, and otherwise this is a plain
        insert with a database-generated key.

        `partial=False` (the default) replaces every field the *schema*
        defines — note that is the schema's fields, not the table's, so
        columns absent from your schema are never touched either way.
        `partial=True` applies only the fields explicitly set on the
        payload, which is PATCH rather than PUT semantics.
        """
        values = self._values(data, partial=partial)
        pk_name = self._pk().key

        if id is None:
            id = values.get(pk_name)
        if id is None:
            return Put(self._to_schema(self._insert(values)), Outcome.CREATED)

        values.pop(pk_name, None)  # the key is addressed by `id`, never patched

        existing = self._get_row_unscoped(id)
        if existing is None:
            try:
                row = self._insert({**values, pk_name: id})
            except IntegrityError:
                # Lost a race: someone inserted this key between our read
                # and our flush. The savepoint in `_insert` has already
                # rolled back, so fall through to the update path.
                existing = self._get_row_unscoped(id)
                if existing is None:
                    raise
            else:
                return Put(self._to_schema(row), Outcome.CREATED)

        if not self._is_visible(id) and not self._may_update_hidden(existing):
            raise RowHiddenError(
                f"{self.row.__name__} {id!r} exists but is excluded by "
                f"{type(self).__name__}._base_query(); refusing to treat it "
                f"as absent. Override _may_update_hidden() to allow this."
            )

        for key, value in values.items():
            setattr(existing, key, value)
        self.session.flush()
        return Put(self._to_schema(existing), Outcome.UPDATED)

    # Thin wrappers over `put`, kept for call sites that want the intent
    # to be explicit. Delete them if you would rather have one door.

    def create(self, data: BaseModel | dict[str, Any]) -> SchemaT:
        """Insert. Raises IntegrityError if the key already exists."""
        return self.put(None, data).value

    def update(self, id: Any, data: BaseModel | dict[str, Any]) -> SchemaT | None:
        """Partial update of an existing row. None if it does not exist."""
        if not self.exists(id):
            return None
        return self.put(id, data, partial=True).value

    def delete(self, id: Any) -> bool:
        row = self.session.scalars(
            self._base_query().where(self._pk() == id)
        ).one_or_none()
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    # ======================================================================
    # Protected core — for subclasses only, never for consumers
    # ======================================================================

    @classmethod
    def _attribute_names(cls) -> frozenset[str]:
        """Mapped attribute names, which may differ from column names."""
        return frozenset(inspect(cls.row).column_attrs.keys())

    @classmethod
    def _pk(cls) -> InstrumentedAttribute[Any]:
        """The single-column primary key as a comparable attribute."""
        mapper = inspect(cls.row)
        columns = mapper.primary_key
        if len(columns) != 1:
            raise ServiceDefinitionError(
                f"{cls.row.__name__} has a composite primary key; override "
                f"get/update/delete on {cls.__name__} to handle it."
            )
        name = mapper.get_property_by_column(columns[0]).key
        return getattr(cls.row, name)

    def _rows_to_schemas(self, stmt: Select[tuple[RowT]]) -> list[SchemaT]:
        return [self._to_schema(r) for r in self.session.scalars(stmt).all()]

    def _where(self, *conditions: Predicate) -> list[SchemaT]:
        """Arbitrary predicates, ANDed. Use to implement public methods.

        def active_since(self, when: date) -> list[User]:
            return self._where(UserRow.last_seen >= when)
        """
        return self._rows_to_schemas(self._base_query().where(*conditions))

    def _one(self, *conditions: Predicate) -> SchemaT | None:
        row = self.session.scalars(self._base_query().where(*conditions)).one_or_none()
        return None if row is None else self._to_schema(row)

    def _find(self, filters: dict[str, Any]) -> list[SchemaT]:
        """`field = value AND ...` from attribute names.

        Keys are checked against the mapping so a name arriving from a
        query string can never reach `getattr` unchecked.
        """
        self._reject_unknown(filters)
        conditions: list[Predicate] = [
            getattr(self.row, k) == v for k, v in filters.items()
        ]
        return self._where(*conditions)

    def _count(self, *conditions: Predicate) -> int:
        inner = self._base_query()
        if conditions:
            inner = inner.where(*conditions)
        return (
            self.session.scalar(select(func.count()).select_from(inner.subquery())) or 0
        )

    def _from_sql(self, sql: str, /, **params: Any) -> list[SchemaT]:
        """Existing raw SQL, mapped objects out, DTOs returned.

        The bridge while migrating off query strings. Must select every
        mapped column. Note this bypasses `_base_query()`, so any scope
        that hook applies must be written into the SQL by hand.
        """
        rows = self.session.scalars(
            select(self.row).from_statement(text(sql)), params
        ).all()
        return [self._to_schema(r) for r in rows]

    def _raw(self, sql: str, /, **params: Any) -> list[Row[Any]]:
        """Untyped escape hatch for SQL that maps to no single entity
        (aggregates, cross-table reports).

        Values are bound parameters. Interpolating them into the string
        is a SQL injection vector; pass them as keywords.
        """
        return list(self.session.execute(text(sql), params).all())

    def _values(
        self, data: BaseModel | dict[str, Any], *, partial: bool
    ) -> dict[str, Any]:
        """Normalise a payload to a validated dict of mapped attributes."""
        if isinstance(data, BaseModel):
            values = data.model_dump(exclude_unset=partial)
        else:
            values = dict(data)
        self._reject_unknown(values)
        return values

    def _get_row_unscoped(self, id: Any) -> RowT | None:
        """Fetch by primary key ignoring `_base_query()`.

        Deliberately unscoped: `put` must know whether the key is *taken*,
        not whether it is visible. Asking the scoped query would report a
        soft deleted row as absent and send us into an insert that fails
        on the primary key.
        """
        return self.session.get(self.row, id)

    def _is_visible(self, id: Any) -> bool:
        return bool(
            self.session.scalar(
                select(self._base_query().where(self._pk() == id).exists())
            )
        )

    def _insert(self, values: dict[str, Any]) -> RowT:
        """Insert inside a savepoint so a conflict is recoverable.

        Without the savepoint an IntegrityError leaves the surrounding
        transaction unusable, so `put` could not fall back to an update.
        """
        savepoint = self.session.begin_nested()
        try:
            row = self.row(**values)
            self.session.add(row)
            self.session.flush()
        except IntegrityError:
            savepoint.rollback()
            raise
        else:
            savepoint.commit()
            return row

    def _compile(self, node: Filter) -> Predicate:
        """Compile a filter tree, after checking its shape.

        Depth and size are bounded before anything is built. A tree
        arriving as JSON from a client is otherwise a cheap way to blow
        the recursion limit or hand the planner a pathological query.
        """
        depth, count = _shape(node)
        if depth > self.max_filter_depth:
            raise ValueError(
                f"filter nests {depth} deep, limit is {self.max_filter_depth}"
            )
        if count > self.max_filter_terms:
            raise ValueError(
                f"filter has {count} nodes, limit is {self.max_filter_terms}"
            )
        return self._build(node)

    def _build(self, node: Filter) -> Predicate:
        match node:
            case Criterion():
                return self._build_criterion(node)
            case Not(term=inner):
                return not_(self._build(inner))
            case Group(junction=junction, terms=terms):
                if not terms:
                    raise ValueError(f"empty {junction.value} group")
                parts = [self._build(t) for t in terms]
                if len(parts) == 1:
                    return parts[0]
                return and_(*parts) if junction is Junction.AND else or_(*parts)
            case _:
                raise TypeError(f"not a filter node: {node!r}")

    def _build_criterion(self, criterion: Criterion) -> Predicate:
        """Turn a `Criterion` into SQL. The only place operators live.

        Both the field and the operator are checked before anything is
        built, so a criterion assembled from an HTTP query string cannot
        reach an unlisted column or an unsupported comparison.
        """
        field = self._check_field(criterion.field, self.filterable, "filter")
        attr = getattr(self.row, field)
        value = criterion.value

        match criterion.op:
            case Op.EQ:
                return attr == value
            case Op.NE:
                return attr != value
            case Op.LT:
                return attr < value
            case Op.LTE:
                return attr <= value
            case Op.GT:
                return attr > value
            case Op.GTE:
                return attr >= value
            case Op.IN:
                if isinstance(value, str | bytes) or not isinstance(value, Sequence):
                    raise ValueError(
                        f"Op.IN on {field!r} needs a sequence, got "
                        f"{type(value).__name__}"
                    )
                if not value:
                    raise ValueError(f"Op.IN on {field!r} needs a non-empty sequence")
                return attr.in_(list(value))
            case Op.LIKE:
                if not isinstance(value, str):
                    raise ValueError(f"Op.LIKE on {field!r} needs a string pattern")
                return attr.like(value)
            case Op.IS_NULL:
                return attr.is_(None) if value else attr.is_not(None)
            case Op.BETWEEN | Op.RANGE:
                lower, upper = self._bounds(field, value)
                # A single supplied bound degrades to one comparison.
                # SQL BETWEEN with a NULL bound yields NULL, matching
                # nothing, which is never what the caller meant.
                if (
                    criterion.op is Op.BETWEEN
                    and lower is not None
                    and upper is not None
                ):
                    return attr.between(lower, upper)
                parts: list[Predicate] = []
                if lower is not None:
                    parts.append(attr >= lower)
                if upper is not None:
                    parts.append(
                        attr <= upper if criterion.op is Op.BETWEEN else attr < upper
                    )
                return parts[0] if len(parts) == 1 else and_(*parts)

    def _bounds(self, field: str, value: Any) -> tuple[Any, Any]:
        """Validate a (lower, upper) pair for BETWEEN/RANGE."""
        if (
            isinstance(value, str | bytes)
            or not isinstance(value, Sequence)
            or len(value) != 2
        ):
            raise ValueError(
                f"range on {field!r} needs a (lower, upper) pair, got {value!r}"
            )
        lower, upper = value
        if lower is None and upper is None:
            raise ValueError(f"range on {field!r} needs at least one bound")
        if lower is not None and upper is not None:
            try:
                inverted = lower > upper
            except TypeError:
                inverted = False  # not orderable in Python; let the DB decide
            if inverted:
                raise ValueError(
                    f"range on {field!r} is inverted: {lower!r} > {upper!r}"
                )
        return lower, upper

    def _compile_sort(self, spec: Sort) -> Any:
        field = self._check_field(spec.field, self.sortable, "sort")
        attr = getattr(self.row, field)
        return attr.desc() if spec.direction is Direction.DESC else attr.asc()

    def _check_field(
        self, field: str, allowed: frozenset[str] | None, action: str
    ) -> str:
        if field not in self._attribute_names():
            raise ValueError(f"{self.row.__name__} has no field {field!r}")
        if allowed is not None and field not in allowed:
            raise ValueError(
                f"{type(self).__name__} does not allow {action} on {field!r}"
            )
        return field

    def _reject_unknown(self, values: dict[str, Any]) -> None:
        unknown = set(values) - self._attribute_names()
        if unknown:
            raise ValueError(f"{self.row.__name__} has no field(s): {sorted(unknown)}")

    # -- niceties -----------------------------------------------------------

    @classmethod
    def bind(cls, session: Session) -> Self:
        """Alias for the constructor, handy in FastAPI dependencies."""
        return cls(session)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(row={self.row.__name__})"


# ---------------------------------------------------------------------------
# What a concrete service looks like
# ---------------------------------------------------------------------------
#
#   # db/models.py — mapped tables (new code; describes tables that exist)
#   class UserRow(Base):
#       __tablename__ = "users"
#       id: Mapped[int] = mapped_column(primary_key=True)
#       name: Mapped[str]
#       email: Mapped[str]
#       deleted_at: Mapped[datetime | None]
#
#   # schemas.py — your existing Pydantic API schemas, plus one line
#   class User(BaseModel):
#       model_config = ConfigDict(from_attributes=True)
#       id: int
#       name: str
#       email: str
#
#   # services/users.py
#   class UserFilter(TypedDict, total=False):
#       id: int
#       name: str
#       email: str
#
#   class UserService(Service[UserRow, User]):
#       # 1. a scope that now applies to get/find/count/list_all/update/delete
#       def _base_query(self):
#           return super()._base_query().where(UserRow.deleted_at.is_(None))
#
#       # 2. statically checked keyword filters for this entity
#       def find(self, **filters: Unpack[UserFilter]) -> list[User]:
#           return self._find(dict(filters))
#
#       # 3. entity-specific queries, built on the protected core
#       def by_email(self, email: str) -> User | None:
#           return self._one(UserRow.email == email)
#
#   # A service needing nothing custom is genuinely one line:
#   class TeamService(Service[TeamRow, Team]):
#       pass
#
#   # An intermediate base that should stay generic must say so:
#   class SoftDeleteService[R: Base, S: BaseModel](Service[R, S], abstract=True):
#       def _base_query(self):
#           return super()._base_query().where(self.row.deleted_at.is_(None))
#
#   class UserService(SoftDeleteService[UserRow, User]):
#       pass
#
#
# ---------------------------------------------------------------------------
# FastAPI wiring
# ---------------------------------------------------------------------------
#
#   def get_users(session: Session = Depends(get_session)) -> UserService:
#       return UserService(session)
#
#   @app.get("/users/{user_id}", response_model=User)
#   def read_user(user_id: int, users: UserService = Depends(get_users)) -> User:
#       user = users.get(user_id)          # -> User | None
#       if user is None:
#           raise HTTPException(404)
#       return user
#
#   @app.get("/users", response_model=list[User])
#   def list_users(
#       name: str | None = None,
#       users: UserService = Depends(get_users),
#   ) -> list[User]:
#       return users.find(name=name) if name else users.list_all(limit=100)
#
#   @app.put("/users/{user_id}", response_model=User)
#   def put_user(
#       user_id: int,
#       payload: UserWrite,
#       response: Response,
#       users: UserService = Depends(get_users),
#   ) -> User:
#       result = users.put(user_id, payload)
#       response.status_code = result.status_code    # 201 or 200
#       return result.value
#
#   @app.patch("/users/{user_id}", response_model=User)
#   def patch_user(
#       user_id: int,
#       payload: UserWrite,
#       users: UserService = Depends(get_users),
#   ) -> User:
#       if not users.exists(user_id):
#           raise HTTPException(404)
#       return users.put(user_id, payload, partial=True).value
#
# The route never touches a session, a row, or a SQL expression.
#
# ---------------------------------------------------------------------------
# On atomicity
# ---------------------------------------------------------------------------
#
# `put` reads, then writes. Under concurrency two callers can both see an
# absent key; the loser's insert raises IntegrityError, and `put` recovers
# by rolling back its savepoint and updating instead. That makes the
# operation correct but not atomic — the losing transaction still performs
# two round trips, and a concurrent *update* can be lost entirely under
# READ COMMITTED.
#
# If contention is real, override `put` with a dialect upsert, which
# resolves the conflict in one statement inside the database:
#
#   from sqlalchemy.dialects.postgresql import insert
#
#   def put(self, id, data, *, partial=False):
#       values = self._values(data, partial=partial)
#       values[self._pk().key] = id
#       stmt = (
#           insert(self.row)
#           .values(**values)
#           .on_conflict_do_update(
#               index_elements=[self._pk()],
#               set_={k: v for k, v in values.items() if k != self._pk().key},
#           )
#           .returning(self.row)
#       )
#       row = self.session.scalars(stmt).one()
#       return Put(self._to_schema(row), Outcome.UPDATED)
#
# The catch is that `ON CONFLICT DO UPDATE` cannot tell you which branch
# it took without extra work (a common trick is comparing `xmax = 0`), so
# the honest `Outcome` is lost. That is the actual trade: one atomic
# statement, or an accurate created/updated flag. Choose per endpoint.
# `on_conflict_do_update` is Postgres/SQLite; MySQL spells it
# `on_duplicate_key_update`, which is why it is not in the generic base.
#
# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
#
# Everything here works on SQLite, with one required setup step and a few
# things worth knowing.
#
# REQUIRED: call `configure_sqlite(engine)` once.
#
#     engine = create_engine("sqlite:///app.db")
#     configure_sqlite(engine)
#     SessionLocal = sessionmaker(engine, expire_on_commit=False)
#
# Without it, `put`'s savepoint recovery is silently unsafe — see that
# function's docstring for the measured difference. This matters most in
# tests, where the common "run each test in a transaction and roll back"
# pattern depends on savepoints actually nesting.
#
# Version floors for the optional upsert override:
#   UPSERT (ON CONFLICT)  SQLite >= 3.24
#   RETURNING             SQLite >= 3.35
# Check with `sqlite3.sqlite_version`. The import differs from Postgres:
#
#     from sqlalchemy.dialects.sqlite import insert
#
# Concurrency reads differently here. SQLite allows one writer at a time
# for the whole database, so the create/update race `put` guards against
# is rarer, and what you see instead is "database is locked". WAL plus
# the busy_timeout above cover most of it; genuinely concurrent writers
# want a different database.
#
# Types to watch: SQLite has no native DATETIME or BOOLEAN, and stores
# them as TEXT/INTEGER. A `Mapped[datetime]` round-trips fine through
# SQLAlchemy, but timezone-aware values do not preserve their offset, and
# raw SQL passed to `_raw()` will see strings. Comparing dates inside
# `_from_sql` needs care that the same SQL on Postgres would not.
#
# `_count()` wraps `_base_query()` in a subquery, which SQLite handles,
# though it will not use an index as well as Postgres would on large
# tables.
#
# One caveat on `find`: the base intentionally does not define it, so a
# subclass declaring `find(**filters: Unpack[UserFilter])` is adding a
# method rather than narrowing an inherited signature — which would be an
# LSP violation that mypy flags. The shared logic lives in `_find`.
