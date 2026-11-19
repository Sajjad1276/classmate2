from aiogram.fsm.state import State, StatesGroup


class RecordWizard(StatesGroup):
    choosing_class = State()
    choosing_subject = State()
    entering_title = State()
    entering_body = State()
    entering_due = State()
    confirming = State()
