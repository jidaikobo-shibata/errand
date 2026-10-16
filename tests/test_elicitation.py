import unittest
from errand.elicitation import UnsupportedForm, browser_url, confirmation_key, content, fields


def schema(**properties):
    return {'type': 'object', 'properties': properties}


class ElicitationTests(unittest.TestCase):
    def test_confirmation_identity_includes_server_message_schema_and_metadata(self):
        request = {'mode': 'form', 'serverName': 'browser', 'message': 'Allow browser_click?',
                   'requestedSchema': schema(), '_meta': {'tool': 'browser_click'}}
        key = confirmation_key(request)
        self.assertIsNotNone(key)
        self.assertEqual(key, confirmation_key({**request, 'threadId': 't', 'turnId': 'next', 'item': {}}))
        for changes in ({'serverName': 'other'}, {'message': 'Allow browser_navigate?'},
                        {'_meta': {'tool': 'browser_navigate'}}):
            self.assertNotEqual(key, confirmation_key({**request, **changes}))
        for changes in ({'mode': 'url'}, {'message': ''}, {'serverName': ''},
                        {'requestedSchema': schema(allow={'type': 'boolean'})}):
            self.assertIsNone(confirmation_key({**request, **changes}))

    def test_empty_confirmation_and_typed_values(self):
        self.assertEqual(content(schema(), {}), {})
        form = schema(name={'type': 'string', 'minLength': 2}, count={'type': 'integer', 'minimum': 1},
                      score={'type': 'number', 'maximum': 2}, allow={'type': 'boolean'})
        form['required'] = ['allow']
        self.assertEqual(content(form, {'name': '確認', 'count': '2', 'score': '1.5', 'allow': False}),
                         {'name': '確認', 'count': 2, 'score': 1.5, 'allow': False})
        with self.assertRaises(ValueError): content(form, {})
        self.assertEqual(content(schema(name={'type': 'string'}), {'name': ''}), {})

    def test_bounds_and_invalid_numbers(self):
        for spec, raw in (({'type': 'integer'}, '1.5'), ({'type': 'number'}, 'NaN'),
                          ({'type': 'number'}, 'Infinity'), ({'type': 'integer', 'minimum': 2}, '1'),
                          ({'type': 'string', 'maxLength': 1}, 'too long')):
            with self.subTest(spec=spec, raw=raw), self.assertRaises(ValueError):
                content(schema(value=spec), {'value': raw})

    def test_enum_values_are_not_display_names(self):
        form = schema(choice={'type': 'string', 'enum': ['yes', 'no'], 'enumNames': ['許可', '拒否']},
                      many={'type': 'array', 'items': {'anyOf': [{'const': 'a', 'title': '項目A'}, {'const': 'b', 'title': '項目B'}]}, 'minItems': 1})
        self.assertEqual(content(form, {'choice': 'yes', 'many': ['b']}), {'choice': 'yes', 'many': ['b']})
        with self.assertRaises(ValueError): content(form, {'choice': '許可'})
        with self.assertRaises(ValueError): content(form, {'many': ['a', 'a']})

    def test_optional_multiselect_can_be_left_unselected(self):
        form = schema(choices={'type': 'array', 'items': {'type': 'string', 'enum': ['a', 'b']},
                               'minItems': 2, 'maxItems': 2})
        self.assertEqual(content(form, {'choices': []}), {})
        self.assertEqual(content(form, {'choices': ['a', 'b']}), {'choices': ['a', 'b']})
        with self.assertRaises(ValueError):
            content(form, {'choices': ['a']})
        with self.assertRaises(ValueError):
            content(form, {'choices': False})
        form['required'] = ['choices']
        with self.assertRaises(ValueError):
            content(form, {'choices': []})
        form['properties']['choices']['minItems'] = 0
        self.assertEqual(content(form, {'choices': []}), {'choices': []})

    def test_formats(self):
        for fmt, good, bad in (('email', 'a@example.com', 'invalid'), ('uri', 'https://example.com', 'plain text'),
                               ('date', '2026-10-16', '2026-99-99'),
                               ('date-time', '2026-10-16T10:00:00Z', '2026-10-16T10:00:00')):
            form = schema(value={'type': 'string', 'format': fmt})
            self.assertEqual(content(form, {'value': good}), {'value': good})
            with self.assertRaises(ValueError): content(form, {'value': bad})

    def test_unknown_constraints_are_not_ignored(self):
        for form in (schema(value={'type': 'object'}), schema(value={'type': 'string', 'pattern': '.*'}),
                     schema(value={'type': 'array', 'items': {'type': 'integer'}}),
                     {'type': 'object', 'properties': {}, 'allOf': []}):
            with self.assertRaises(UnsupportedForm): fields(form)

    def test_browser_url_rejects_executable_and_ambiguous_targets(self):
        for url in ('javascript:alert(1)', 'file:///tmp/a', 'data:text/html,hi',
                    'https://user:pass@example.com/', 'https://@example.com/', 'https://example.com/\n',
                    'https://example.com:bad/', 'https://example.com\\@other.test/', None):
            with self.subTest(url=url), self.assertRaises(ValueError): browser_url(url)
        self.assertEqual(browser_url('http://localhost:8080/confirm'), 'http://localhost:8080/confirm')
