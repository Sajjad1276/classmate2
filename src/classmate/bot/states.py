from aiogram.fsm.state import State, StatesGroup


class HomeworkForm(StatesGroup):
    class_id = State()
    subject = State()
    title = State()
    description = State()
    due = State()


class ExamForm(StatesGroup):
    class_id = State()
    subject = State()
    title = State()
    scope = State()
    when = State()


class ReportForm(StatesGroup):
    class_id = State()
    body = State()
    title = State()


class SchoolForm(StatesGroup):
    name = State()
    year = State()


class ClassForm(StatesGroup):
    school_id = State()
    grade = State()
    section = State()
    name = State()


class JoinForm(StatesGroup):
    code = State()


class ConnectForm(StatesGroup):
    code = State()


class SubjectForm(StatesGroup):
    class_id = State()
    name = State()


class TimetableForm(StatesGroup):
    class_id = State()
    day = State()
    period = State()
    subject = State()
    time_range = State()


class SearchForm(StatesGroup):
    query = State()


class AnnouncementForm(StatesGroup):
    class_id = State()
    title = State()
    body = State()


class EditRecordForm(StatesGroup):
    first = State()
    second = State()


class SubmissionForm(StatesGroup):
    homework_id = State()
    answer = State()


class EvaluationForm(StatesGroup):
    data = State()


class PollCreateForm(StatesGroup):
    question = State()
    options = State()


class ReviewSubmissionForm(StatesGroup):
    feedback = State()
