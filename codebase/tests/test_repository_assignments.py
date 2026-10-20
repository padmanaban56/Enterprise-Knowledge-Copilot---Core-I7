import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from api import auth
from api.access_control import allowed_repositories, payload_visible, repository_roles


class RepositoryAssignmentTests(unittest.TestCase):
    def test_department_accounts_get_only_their_mapped_repositories(self):
        for roles, expected in [(['HR'], ['HR']), (['IT'], ['IT']),
                                (['ENGINEERING', 'PROJECTS'], ['Engineering','Projects']),
                                (['FINANCE'], ['Finance']), (['EMPLOYEE'], [])]:
            self.assertEqual(allowed_repositories(roles), expected)
        self.assertEqual(len(allowed_repositories(['ADMIN'])), 6)

    def test_broad_legacy_document_roles_do_not_override_repository_membership(self):
        self.assertFalse(payload_visible({'repository':'Finance', 'access_roles':['EMPLOYEE','HR','FINANCE']}, ['HR','EMPLOYEE']))
        self.assertTrue(payload_visible({'repository':'HR', 'access_roles':['HR']}, ['HR']))
        self.assertFalse(payload_visible({'repository':'External', 'access_roles':[]}, ['HR']))
        self.assertEqual(repository_roles('Engineering'), ['ENGINEERING'])


class CurrentAccountPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_or_role_reset_invalidates_old_tokens(self):
        service = MagicMock()
        service.get_user_by_id.return_value = {'user_id':'u','email':'hr@company.com','username':'HR',
            'department':'HR','role':'employee','access_roles':['HR'],'is_active':True,'auth_version':2}
        with patch.object(auth, 'decode_token', return_value={'sub':'u','auth_version':1,'role':'admin'}), \
             patch.object(auth, 'get_auth_service', return_value=service):
            with self.assertRaises(HTTPException) as error:
                await auth.get_current_user('old-token')
            self.assertEqual(error.exception.status_code, 401)

    async def test_current_database_roles_override_stale_role_claims(self):
        service = MagicMock()
        service.get_user_by_id.return_value = {'user_id':'u','email':'it@company.com','username':'IT',
            'department':'IT','role':'employee','access_roles':['IT'],'is_active':True,'auth_version':2}
        with patch.object(auth, 'decode_token', return_value={'sub':'u','auth_version':2,'role':'admin','access_roles':['ADMIN']}), \
             patch.object(auth, 'get_auth_service', return_value=service):
            user = await auth.get_current_user('current-token')
        self.assertEqual(user.role, 'employee')
        self.assertEqual(user.access_roles, ['IT'])


if __name__ == '__main__':
    unittest.main()
