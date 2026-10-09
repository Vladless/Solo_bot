from sqlalchemy.orm import declarative_base


Base = declarative_base()


class DictLikeMixin:
    """Позволяет обращаться к объектам моделей как к словарям."""

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}
