"""One locked source-authorization path for every classroom-bound task.

Classroom moves preserve its immutable material pointer. Current course and
original material course can therefore differ, but both remain owner-scoped and
must be live. Optional input IDs assert equality; they never select authorization.
"""
import sqlalchemy as sa

from ..domain.data import Gone, NotFound, RevisionConflict
from .schema import tables


class PgTaskSources:
    def __init__(self, connection, owner_id):
        self.connection, self.owner_id = connection, owner_id

    def _rows(self, name, identities, *, lock=False):
        table = tables[name]
        fields = {
            'class_sessions': ('id', 'course_id', 'course_material_version_id', 'revision', 'deleted_at'),
            'course_material_versions': ('id', 'course_id'),
            'courses': ('id', 'semester_id', 'revision', 'deleted_at'),
            'semesters': ('id', 'revision', 'deleted_at'),
        }[name]
        query = sa.select(*(table.c[field] for field in fields)).where(table.c.owner_id == self.owner_id,
            table.c.id.in_(identities)).order_by(table.c.id)
        if lock:
            query = query.with_for_update(read=True)
        rows = self.connection.execute(query).mappings().all()
        if len(rows) != len(set(identities)):
            raise NotFound('task source not found')
        return {row['id']: row for row in rows}

    def authorize(self, class_session_id, reference):
        if class_session_id is None:
            if set(reference) - {'schema_version'}:
                raise ValueError('source references require an explicit classroom')
            return
        classroom = self._rows('class_sessions', [class_session_id])[class_session_id]
        material_id = classroom['course_material_version_id']
        material = self._rows('course_material_versions', [material_id])[material_id]
        if (reference.get('class_session_id', str(class_session_id)) != str(class_session_id)
                or reference.get('course_material_version_id', str(material_id)) != str(material_id)):
            raise ValueError('input references must match server-persisted classroom sources')

        course_ids = {classroom['course_id'], material['course_id']}
        initial_courses = self._rows('courses', course_ids)
        semester_ids = {row['semester_id'] for row in initial_courses.values()}
        # Task transactions use the same ancestor-before-child order as PR-02a
        # revocation. Sort the complete sets before acquiring any resource lock;
        # do not discover/lock the original course after locking the classroom.
        semesters = self._rows('semesters', semester_ids, lock=True)
        courses = self._rows('courses', course_ids, lock=True)
        if any(row['semester_id'] != initial_courses[identity]['semester_id']
               for identity, row in courses.items()):
            raise RevisionConflict(max(row['revision'] for row in courses.values()))
        locked_class = self._rows('class_sessions', [class_session_id], lock=True)[class_session_id]
        if (locked_class['course_id'] != classroom['course_id']
                or locked_class['course_material_version_id'] != material_id):
            raise RevisionConflict(locked_class['revision'])

        if any(row['deleted_at'] is not None for row in [locked_class, *courses.values(), *semesters.values()]):
            raise Gone('task source or ancestor revoked')
        tombstones = tables['deletion_tombstones']
        revoked = self.connection.scalar(sa.select(tombstones.c.id).where(
            tombstones.c.owner_id == self.owner_id,
            sa.or_(
                sa.and_(tombstones.c.resource_type == 'account', tombstones.c.resource_id == self.owner_id),
                sa.and_(tombstones.c.resource_type == 'class_session', tombstones.c.resource_id == class_session_id),
                sa.and_(tombstones.c.resource_type == 'course_material_version', tombstones.c.resource_id == material_id),
                sa.and_(tombstones.c.resource_type == 'course', tombstones.c.resource_id.in_(course_ids)),
                sa.and_(tombstones.c.resource_type == 'semester', tombstones.c.resource_id.in_(semester_ids)),
            )).limit(1))
        if revoked:
            raise Gone('task source or ancestor revoked')
