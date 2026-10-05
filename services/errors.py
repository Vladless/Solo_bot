class ServiceError(Exception):
    """Базовая ошибка бизнес-логики."""

    def __init__(self, message: str, code: str = "error") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class NotFoundError(ServiceError):
    """Ресурс не найден."""

    def __init__(self, message: str = "Не найдено") -> None:
        super().__init__(message, code="not_found")


class ValidationError(ServiceError):
    """Невалидные входные данные или бизнес-правило нарушено."""

    def __init__(self, message: str = "Ошибка валидации") -> None:
        super().__init__(message, code="validation_error")


class InsufficientFundsError(ServiceError):
    """Недостаточно средств на балансе."""

    def __init__(self, message: str = "Недостаточно средств", required: float = 0, balance: float = 0) -> None:
        self.required = required
        self.balance = balance
        super().__init__(message, code="insufficient_funds")


class LimitExceededError(ServiceError):
    """Превышен лимит (купоны, использования, и т.д.)."""

    def __init__(self, message: str = "Лимит исчерпан") -> None:
        super().__init__(message, code="limit_exceeded")


class ForbiddenError(ServiceError):
    """Операция запрещена настройками или правами."""

    def __init__(self, message: str = "Операция запрещена") -> None:
        super().__init__(message, code="forbidden")
