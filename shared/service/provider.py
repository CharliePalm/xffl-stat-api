from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from shared.model import Provider
from shared.service.service import Criterion, Filter, Service, filters_model
from shared.service.sql_model import BaseSQLModel


class ProviderModel(BaseSQLModel):
    __tablename__ = "provider"

    name: Mapped[str] = mapped_column(primary_key=True)
    pos: Mapped[int]


ProviderFilters = filters_model(Provider, name="ProviderFilters")


class ProviderService(Service[ProviderModel, Provider]):
    schema = Provider
    row = ProviderModel

    def _criterion_for(self, field: str, value: Any) -> Filter:
        if field == "name":
            return Criterion.like("name", f"%{value}%")
        return super()._criterion_for(field, value)
