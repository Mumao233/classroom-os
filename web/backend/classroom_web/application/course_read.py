"""Existing course-detail read through current session/owner authorization."""
from uuid import UUID
import sqlalchemy as sa
from ..domain.auth import AuthReply,AuthRejected
from ..domain.data import NotFound,Gone
from ..domain.identity import AuthorizationDenied
from ..infrastructure.schema import tables


class CourseReader:
    def __init__(self,*,auth,database):self.auth=auth;self.database=database

    def course(self,ctx,course_id,revision=None):
        try:
            resource=UUID(course_id)
            if str(resource)!=course_id:raise ValueError()
            principal=self.auth.principal(ctx)
            with self.database.authorized_transaction(principal,activity=True) as tx:
                course=tx.catalog.get_course(resource)
                if revision is not None and revision!=course.revision:raise AuthRejected('REVISION_CONFLICT',409)
                sessions=tables['class_sessions']
                count,last=tx.connection.execute(sa.select(sa.func.count(),sa.func.max(sessions.c.started_at)).where(
                    sessions.c.owner_id==principal.user_id,sessions.c.course_id==resource,sessions.c.deleted_at.is_(None))).one()
                body=dict(id=str(course.id),revision=course.revision,created_at=course.created_at.isoformat(),updated_at=course.updated_at.isoformat(),semester_id=str(course.semester_id),
                    name=course.name,english_name=course.english_name or '',language=course.language,teacher=course.teacher or '',room=course.room or '',
                    color_token=course.color_token,icon_token=course.icon_key,archived=course.archived_at is not None,class_session_count=count,last_class_session_at=last.isoformat() if last else None)
            return AuthReply(body)
        except (ValueError,NotFound):raise AuthRejected('NOT_FOUND',404) from None
        except Gone:raise AuthRejected('GONE',410) from None
        except AuthorizationDenied:raise AuthRejected() from None
