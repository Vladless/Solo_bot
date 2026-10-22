from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.email_binding import discard_email_binding_code


class EmailBindingState(StatesGroup):
    waiting_for_email = State()
    waiting_for_code = State()


async def clear_email_binding_context(state: FSMContext, request_id: str | None = None) -> None:
    """Отменяет активную привязку и удаляет её код."""
    if await state.get_state() not in (
        EmailBindingState.waiting_for_email.state,
        EmailBindingState.waiting_for_code.state,
    ):
        return
    data = await state.get_data()
    if request_id is not None and data.get("email_binding_request_id") != request_id:
        return
    await state.clear()
    identity_id = data.get("email_binding_identity_id")
    email = data.get("email_binding_email")
    request_id = data.get("email_binding_request_id")
    if identity_id and email and request_id:
        await discard_email_binding_code(str(identity_id), str(email), str(request_id))
