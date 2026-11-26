from aiogram.fsm.state import State, StatesGroup


class JoinForm(StatesGroup):
    code = State()


class SearchForm(StatesGroup):
    query = State()


class SubmissionForm(StatesGroup):
    homework_id = State()
    answer = State()


class GroupLinkForm(StatesGroup):
    code = State()
    selecting = State()
