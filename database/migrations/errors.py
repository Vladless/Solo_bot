DEPENDENCY_ERRORCODE = "2BP01"
UNIQUE_VIOLATION_ERRORCODE = "23505"

ALREADY_DONE_ERRORCODES = frozenset({"42701", "42P07", "42710", "42704", "42P01"})
CATALOG_RELATIONS = ("pg_class", "pg_index", "pg_constraint", "pg_type")


def error_code(exc: BaseException) -> str:
    """Код ошибки PostgreSQL: psycopg2 держит его в pgcode, asyncpg — в sqlstate."""
    for error in (exc, getattr(exc, "orig", None), getattr(exc, "__cause__", None)):
        if error is None:
            continue
        for code in (getattr(error, "pgcode", ""), getattr(error, "sqlstate", "")):
            if code:
                return str(code)
    return ""


def is_dependency_error(exc: BaseException) -> bool:
    """Объект снять нельзя: на него опираются другие — «cannot drop … because other objects depend on it»."""
    return error_code(exc) == DEPENDENCY_ERRORCODE


def is_already_done(exc: BaseException) -> bool:
    """Шаг не нужен: объект уже есть или уже снят.

    Так выглядит одновременный старт двух ботов на одной базе: второй получает «уже существует»,
    а конфликт по системному каталогу — это гонка на создании индекса, а не проблема данных.
    """
    if error_code(exc) in ALREADY_DONE_ERRORCODES:
        return True
    if error_code(exc) != UNIQUE_VIOLATION_ERRORCODE:
        return False
    message = str(getattr(exc, "orig", exc))
    return any(relation in message for relation in CATALOG_RELATIONS)
